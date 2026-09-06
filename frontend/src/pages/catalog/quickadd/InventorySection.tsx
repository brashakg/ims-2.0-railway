// Quick Add - INVENTORY. The reorder level (stock, SKU and barcode are all
// automatic) plus the Product Images block: upload, drag-and-drop, the
// variant photo copy, remove and remove-background. `#product-images` is the
// anchor the catalog list's "Add photo" deep link scrolls to.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { Boxes, ImageIcon, Loader2, Upload, Wand2, X } from 'lucide-react';
import clsx from 'clsx';
import { Section } from './parts';
import type { QuickAddForm } from './useQuickAddForm';

export function InventorySection({ form }: { form: QuickAddForm }) {
  const {
    isReviewMode, reorderLevel, setReorderLevel, images, setImages, variantCtx,
    imageInputRef, onImageInputChange, onImageDrop, dragActive, setDragActive,
    uploadingImages, editingImages, removeImage, editImage,
    openSections, toggleSection, sectionIssues,
  } = form;

  return (
    <Section
      id="inventory"
      title="Inventory"
      icon={<Boxes className="w-5 h-5" />}
      subtitle={
        isReviewMode
          ? 'Product images (stock & reorder arrive after approval)'
          : 'Reorder level (stock, SKU & barcode are automatic)'
      }
      open={openSections.inventory}
      issues={sectionIssues.inventory}
      onToggle={toggleSection}
    >
      {/* Reorder level is a SPINE setting — an imported doc has none
          until approval creates the billing row, so it's suppressed in
          review mode. */}
      {isReviewMode ? (
        <p className="text-xs text-gray-500 mt-2">
          Stock and reorder settings come after approval — approving creates the sellable
          billing product; stock then arrives via Goods Receipt (GRN).
        </p>
      ) : (
        // Action-first: the one sentence you can act on leads, and the
        // explainer sits BESIDE the lone input instead of under three
        // empty columns.
        <div className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,3fr)] gap-3 tablet:gap-4 items-start">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-reorder_level">
              Reorder Level
            </label>
            <input
              id="qa-field-reorder_level"
              type="number"
              title="Reorder Level"
              placeholder="5"
              value={reorderLevel}
              onChange={(e) => setReorderLevel(e.target.value)}
              className="input-field w-full"
              min="0"
            />
          </div>
          <p className="text-xs text-gray-600 tablet:pt-5">
            <span className="font-semibold text-gray-800">
              Set the reorder level and you&apos;ll be alerted when stock falls below it.
            </span>{' '}
            Stock is added via Goods Receipt (GRN), not here. The SKU is assigned when the
            product is created and the internal barcode at goods receipt — neither is typed.
          </p>
        </div>
      )}

      {/* Product images — real upload (durably stored + served by the
          backend; the create payload sends the resulting URLs). The id
          is the #images anchor the catalog list's "Add photo" lands on. */}
      <div id="product-images" className="mt-4 pt-4 border-t border-gray-100 scroll-mt-4">
        <label className="block text-xs font-medium text-gray-700 mb-2">Product Images</label>

        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          title="Upload product images"
          onChange={onImageInputChange}
        />

        <div
          onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
          onDrop={onImageDrop}
          onClick={() => imageInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') imageInputRef.current?.click(); }}
          className={clsx(
            'border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors',
            dragActive ? 'border-bv bg-bv-50' : 'border-gray-300 hover:border-gray-400'
          )}
        >
          {uploadingImages ? (
            <Loader2 className="w-10 h-10 mx-auto text-bv mb-2 animate-spin" />
          ) : (
            <ImageIcon className="w-10 h-10 mx-auto text-gray-400 mb-2" />
          )}
          <p className="text-gray-500">
            {uploadingImages ? 'Uploading…' : 'Drag and drop images here, or click to browse'}
          </p>
          <span className="btn-outline mt-3 inline-flex items-center pointer-events-none">
            <Upload className="w-4 h-4 mr-2" />
            Upload Images
          </span>
        </div>

        {/* Variant mode: photos are never auto-copied (they usually show
            the SIBLING's colour) — offer them as a one-click copy. */}
        {variantCtx && variantCtx.sourceImages.length > 0 && (
          <button
            type="button"
            onClick={() =>
              setImages((prev) =>
                Array.from(new Set([...prev, ...variantCtx.sourceImages]))
              )
            }
            className="mt-3 btn-secondary flex items-center gap-2 text-sm"
            title="Photos usually show the sibling's colour — copy only if they apply"
          >
            <ImageIcon className="w-4 h-4" />
            Copy {variantCtx.sourceImages.length} photo
            {variantCtx.sourceImages.length === 1 ? '' : 's'} from{' '}
            {variantCtx.sourceLabel}
          </button>
        )}

        {images.length > 0 && (
          <div className="mt-3 grid grid-cols-3 tablet:grid-cols-4 laptop:grid-cols-6 desktop:grid-cols-8 gap-3">
            {images.map((url) => {
              // Every image is editable now: an external URL
              // is re-hosted into our store first, then cleaned.
              const isEditing = editingImages.has(url);
              return (
              <div key={url} className="relative group aspect-square rounded-lg overflow-hidden border border-gray-200">
                <img
                  src={url}
                  alt="Product"
                  className="w-full h-full object-cover"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = '0.3'; }}
                />
                {/* Both photo controls are real 40px tap targets: they
                    were the two smallest things on the page, and one of
                    them is destructive. */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); removeImage(url); }}
                  aria-label="Remove image"
                  title="Remove image"
                  className="absolute top-1 right-1 inline-flex min-h-10 min-w-10 items-center justify-center rounded-full bg-white/90 text-gray-600 hover:text-red-600 shadow"
                >
                  <X className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); void editImage(url); }}
                  disabled={isEditing}
                  aria-label="Remove background"
                  title="Remove background (clean up + resize)"
                  className="absolute bottom-1 left-1 inline-flex min-h-10 items-center gap-1 px-2.5 rounded-md bg-white/90 text-gray-700 hover:text-bv shadow text-[11px] font-medium disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {isEditing ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Wand2 className="w-3.5 h-3.5" />
                  )}
                  <span>{isEditing ? 'Working…' : 'Remove bg'}</span>
                </button>
              </div>
              );
            })}
          </div>
        )}
      </div>
    </Section>
  );
}
