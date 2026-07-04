// ============================================================================
// IMS 2.0 - Duplicate-product rescue popup (Hub Phase 1 / variant assist)
// ============================================================================
// Shown when a product create is refused with the DUPLICATE_PRODUCT 409. The
// enriched conflict payload (see backend product_master._duplicate_error)
// carries the EXISTING row's display fields, so this popup shows the product
// the operator just collided with and offers the three rescue paths:
//
//   1. "Add a new colour/size of this model"  (default — Enter)
//      -> flips the Add-Product form into VARIANT MODE seeded from the
//         existing product (handled by the caller via onAddVariant).
//   2. "Open the existing product"
//      -> navigates to the catalog list focused on that SKU (caller-provided).
//   3. "Go back"                              (Esc)
//      -> just closes; the form behind is left FULLY intact.
//
// There is deliberately NO "create anyway" — the owner ruled duplicates are
// never creatable. Extracted as its own component (not inline in QuickAddPage)
// per the components-outside-components rule.

import { useEffect, useRef } from 'react';
import { CopyPlus, ExternalLink, Undo2, ImageOff, AlertTriangle } from 'lucide-react';
import type { DuplicateProductInfo } from '../../services/api/products';
import { resolveApiAssetUrl } from '../../services/api/client';

export interface DuplicateProductModalProps {
  /** The 409 `existing` payload. */
  info: DuplicateProductInfo;
  /** Primary action (Enter): flip the form into variant mode from this product. */
  onAddVariant: () => void;
  /** Navigate to the existing product's row in the catalog. */
  onOpenExisting: () => void;
  /** Close and leave the form untouched (Esc / Go back / backdrop). */
  onClose: () => void;
  /** Disables the actions while the variant seed is being fetched. */
  busy?: boolean;
}

/** Rupee display for the price line; '' when absent. */
function rupees(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(Number(v))
    ? ''
    : `₹${Number(v).toLocaleString('en-IN')}`;
}

/** Small active/draft/inactive status note for the existing row. */
function statusNote(info: DuplicateProductInfo): { text: string; tone: 'ok' | 'warn' } {
  if (info.is_active === false) return { text: 'Inactive (archived)', tone: 'warn' };
  if (String(info.catalog_status || '').toUpperCase() === 'DRAFT') {
    return { text: 'Draft — in your catalog, not sellable yet', tone: 'warn' };
  }
  return { text: 'Active in your catalog', tone: 'ok' };
}

export function DuplicateProductModal({
  info,
  onAddVariant,
  onOpenExisting,
  onClose,
  busy = false,
}: DuplicateProductModalProps) {
  const primaryRef = useRef<HTMLButtonElement | null>(null);

  // Keyboard contract: Enter = default action (add variant), Esc = go back.
  // Bound on window so it works no matter where focus sits; the parent form's
  // own Ctrl+Enter save shortcut is suppressed while this popup is open.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      } else if (e.key === 'Enter' && !busy) {
        e.preventDefault();
        e.stopPropagation();
        onAddVariant();
      }
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [onAddVariant, onClose, busy]);

  // Land focus on the primary action so Enter/Tab flow starts there.
  useEffect(() => {
    primaryRef.current?.focus();
  }, []);

  const name =
    info.name || [info.brand, info.model].filter(Boolean).join(' ') || 'this product';
  const imageUrl = info.image_url ? resolveApiAssetUrl(info.image_url) : '';
  const note = statusNote(info);
  const priceLine = [
    rupees(info.mrp) && `MRP ${rupees(info.mrp)}`,
    rupees(info.offer_price) &&
      info.offer_price !== info.mrp &&
      `Offer ${rupees(info.offer_price)}`,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Product already exists"
        className="bg-white rounded-xl shadow-xl w-full max-w-md overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-100 flex items-start gap-3">
          <span className="mt-0.5 p-1.5 rounded-lg bg-amber-100 text-amber-700 shrink-0">
            <AlertTriangle className="w-4 h-4" />
          </span>
          <div>
            <h2 className="text-base font-bold text-gray-900">
              This product already exists
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Same brand + model + colour{info.size ? ' + size' : ''} as an existing
              SKU — duplicates can&apos;t be created.
            </p>
          </div>
        </div>

        {/* Existing product summary */}
        <div className="px-5 py-4">
          <div className="flex items-start gap-3 rounded-lg border border-gray-200 bg-gray-50/60 p-3">
            {imageUrl ? (
              <img
                src={imageUrl}
                alt={name}
                className="w-16 h-16 rounded-lg object-cover border border-gray-200 bg-white shrink-0"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <span className="w-16 h-16 rounded-lg border border-gray-200 bg-white flex items-center justify-center text-gray-300 shrink-0">
                <ImageOff className="w-6 h-6" />
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-gray-900 text-sm truncate">{name}</p>
              <p className="text-xs text-gray-500 mt-0.5 truncate">
                {info.sku ? <>SKU <span className="font-medium text-gray-700">{info.sku}</span></> : null}
                {info.colour_code ? <> · Colour {info.colour_code}</> : null}
                {info.size ? <> · Size {info.size}</> : null}
              </p>
              {priceLine && <p className="text-xs text-gray-600 mt-0.5">{priceLine}</p>}
              <p
                className={
                  note.tone === 'ok'
                    ? 'text-xs text-emerald-600 mt-0.5'
                    : 'text-xs text-amber-600 mt-0.5'
                }
              >
                {note.text}
              </p>
            </div>
          </div>

          {/* Actions */}
          <div className="mt-4 space-y-2">
            <button
              ref={primaryRef}
              type="button"
              disabled={busy}
              onClick={onAddVariant}
              className="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <CopyPlus className="w-4 h-4" />
              {busy ? 'Loading…' : 'Add a new colour/size of this model'}
              {!busy && <kbd className="qa-kbd ml-1">Enter</kbd>}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onOpenExisting}
              className="btn-secondary w-full flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <ExternalLink className="w-4 h-4" />
              Open the existing product
            </button>
            <button
              type="button"
              onClick={onClose}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100"
            >
              <Undo2 className="w-4 h-4" />
              Go back <kbd className="qa-kbd ml-1">Esc</kbd>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default DuplicateProductModal;
