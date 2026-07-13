// ============================================================================
// IMS 2.0 - Catalog Manager: product slide-over drawer
// ============================================================================
// Right-side slide-over opened from the Catalog Manager photo grid. Preserves
// the grid's scroll + filters (it overlays, never navigates). Two modes:
//
//   kind='spine'    — a billing/POS product (`products` collection): read-only
//                     facts + actions Edit (/catalog/add?edit=), Clone
//                     (/catalog/add?clone=), Order stock (Buy Desk deep link).
//   kind='imported' — a catalog_products doc awaiting review (BVI import):
//                     the REVIEW panel runs a promote dry-run and renders a
//                     gap checklist + a quick-fix mini-form (category, the
//                     registry's required attributes, MRP/offer, HSN/GST)
//                     saving through PUT /catalog/products/{id}; "Approve for
//                     POS" enables the moment the dry-run passes and
//                     auto-advances to the next review item.
//
// ArrowLeft / ArrowRight move to the previous / next card (suppressed while
// the image lightbox is open or an input has focus). Images render lazily
// with referrerPolicy="no-referrer" and fall back to a Package placeholder
// (BVI /uploads URLs may 404 until the rehost pass runs).

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  X,
  Package,
  Pencil,
  CopyPlus,
  ShoppingCart,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Archive,
  ArchiveRestore,
  ShieldCheck,
  Globe,
} from 'lucide-react';
import clsx from 'clsx';
import { ImageLightbox } from '../../components/common/ImageLightbox';
import { useToast } from '../../context/ToastContext';
// Import DIRECT from the modules (not the services/api barrel — TS2614).
import {
  catalogProductsApi,
  type CatalogProductDoc,
  type PromoteDryRunResult,
  type UpdateCatalogProductPayload,
} from '../../services/api/catalog';
import {
  CATEGORY_BROWSE_OPTIONS,
  canonicalCategory,
  categoryBrowseLabel,
} from '../../utils/categoryNormalize';
import {
  CANONICAL_TO_PICKER,
  getCategoryFields,
  loadCategoryRegistry,
  type CategoryField,
} from './productAddShared';
import { writeReviewQueue } from './reviewQueue';

// ---------------------------------------------------------------------------
// Shared item shape with CatalogManagerPage.
// ---------------------------------------------------------------------------
export type CatalogItemKind = 'spine' | 'imported';

export interface CatalogDrawerItem {
  kind: CatalogItemKind;
  doc: Record<string, unknown>;
}

interface CatalogProductDrawerProps {
  item: CatalogDrawerItem;
  onClose: () => void;
  /** Move to the previous/next card in the grid (undefined = edge). */
  onPrev?: () => void;
  onNext?: () => void;
  /** An imported doc was edited/archived — sync the grid row. */
  onUpdated?: (doc: CatalogProductDoc) => void;
  /** An imported doc was APPROVED (spine row created) — parent toasts,
   *  refreshes the queue and auto-advances. */
  onApproved?: (productId: string, sku?: string | null) => void;
  /** "Edit everything" — open the FULL cataloguing page (?review=<id>). The
   *  parent stashes the whole grid page as the review queue; without it the
   *  drawer falls back to a single-item stash. */
  onEditEverything?: (productId: string) => void;
}

const str = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return s === 'NaN' ? '' : s;
};

const fmtINR = (n: unknown): string => {
  const num = typeof n === 'number' ? n : parseFloat(str(n));
  if (!Number.isFinite(num)) return '—';
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(num);
  } catch {
    return String(Math.round(num));
  }
};

const humanKey = (k: string): string =>
  k
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());

export function docId(doc: Record<string, unknown>): string {
  return str(doc.product_id || doc.id || doc._id);
}

export function docName(doc: Record<string, unknown>): string {
  const name = str(doc.name || doc.title);
  if (name) return name;
  const brand = str(doc.brand || (doc.attributes as Record<string, unknown>)?.brand_name);
  const model = str(doc.model || (doc.attributes as Record<string, unknown>)?.model_no);
  return [brand, model].filter(Boolean).join(' ') || str(doc.sku) || 'Untitled product';
}

export function docImages(doc: Record<string, unknown>): string[] {
  const images = Array.isArray(doc.images)
    ? (doc.images as unknown[]).map((u) => str(u)).filter(Boolean)
    : [];
  if (images.length > 0) return images;
  const single = str(doc.image_url);
  return single ? [single] : [];
}

export function docMrp(doc: Record<string, unknown>): number | null {
  const pricing = (doc.pricing || {}) as Record<string, unknown>;
  const v = pricing.mrp ?? doc.mrp;
  const num = typeof v === 'number' ? v : parseFloat(str(v));
  return Number.isFinite(num) ? num : null;
}

export function docOffer(doc: Record<string, unknown>): number | null {
  const pricing = (doc.pricing || {}) as Record<string, unknown>;
  const v = pricing.offer_price ?? doc.offer_price;
  const num = typeof v === 'number' ? v : parseFloat(str(v));
  return Number.isFinite(num) ? num : null;
}

// ---------------------------------------------------------------------------
// Small presentational bits (module level — never nested components).
// ---------------------------------------------------------------------------
function FactRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <dt className="text-xs text-gray-500 shrink-0">{label}</dt>
      <dd className="text-xs font-medium text-gray-900 text-right break-words min-w-0">
        {value || '—'}
      </dd>
    </div>
  );
}

function GroupTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mt-4 mb-1">
      {children}
    </h3>
  );
}

// ===========================================================================
// Drawer
// ===========================================================================
export function CatalogProductDrawer({
  item,
  onClose,
  onPrev,
  onNext,
  onUpdated,
  onApproved,
  onEditEverything,
}: CatalogProductDrawerProps) {
  const navigate = useNavigate();
  const toast = useToast();

  // Local copy of the doc so a review-form save can refresh it in place.
  const [doc, setDoc] = useState<Record<string, unknown>>(item.doc);
  useEffect(() => setDoc(item.doc), [item.doc]);

  const id = docId(doc);
  const name = docName(doc);
  const images = docImages(doc);
  const isImported = item.kind === 'imported';
  const attributes = (doc.attributes || {}) as Record<string, unknown>;
  const ecom = (doc.ecom || null) as Record<string, unknown> | null;

  const [lightboxAt, setLightboxAt] = useState<number | null>(null);

  // ---- Review state (imported only) ----------------------------------------
  const [dry, setDry] = useState<PromoteDryRunResult | null>(null);
  const [dryLoading, setDryLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [registryReady, setRegistryReady] = useState(false);

  // Mini-form state, seeded from the doc.
  const [formCategory, setFormCategory] = useState('');
  const [formAttrs, setFormAttrs] = useState<Record<string, string>>({});
  const [formMrp, setFormMrp] = useState('');
  const [formOffer, setFormOffer] = useState('');
  const [formHsn, setFormHsn] = useState('');
  const [formGst, setFormGst] = useState('');
  // Track explicit HSN/GST edits: an untouched pair is OMITTED from the save
  // so a category change re-derives both server-side.
  const [taxTouched, setTaxTouched] = useState(false);

  const needsReview = Boolean(doc.needs_review);
  const inactive = doc.is_active === false;

  const seedForm = useCallback((d: Record<string, unknown>) => {
    const attrs = (d.attributes || {}) as Record<string, unknown>;
    setFormCategory(canonicalCategory(str(d.category)));
    const seeded: Record<string, string> = {};
    Object.keys(attrs).forEach((k) => {
      seeded[k] = str(attrs[k]);
    });
    setFormAttrs(seeded);
    const mrp = docMrp(d);
    const offer = docOffer(d);
    setFormMrp(mrp === null ? '' : String(mrp));
    setFormOffer(offer === null ? '' : String(offer));
    setFormHsn(str(d.hsn_code));
    setFormGst(str(d.gst_rate));
    setTaxTouched(false);
  }, []);

  const runDryRun = useCallback(
    async (productId: string) => {
      setDryLoading(true);
      try {
        const res = await catalogProductsApi.promoteDryRun(productId);
        setDry(res);
      } catch (e: unknown) {
        // A hard 409 (already a billing product / SKU clash) surfaces here.
        setDry({
          ok: false,
          gaps: [
            {
              field: null,
              message:
                e instanceof Error && e.message
                  ? e.message
                  : 'Could not check this product — try again.',
            },
          ],
          duplicate_warnings: [],
        });
      } finally {
        setDryLoading(false);
      }
    },
    []
  );

  // On open / item change: seed the mini-form + fire the dry-run.
  useEffect(() => {
    if (!isImported || !id) return;
    seedForm(item.doc);
    setDry(null);
    void runDryRun(id);
  }, [isImported, id, item.doc, seedForm, runDryRun]);

  // Registry (required flags + dictionary selects) — shared module cache.
  useEffect(() => {
    if (!isImported) return;
    let alive = true;
    loadCategoryRegistry()
      .then(() => {
        if (alive) setRegistryReady(true);
      })
      .catch(() => {
        /* local fallback flags */
      });
    return () => {
      alive = false;
    };
  }, [isImported]);

  // Required attribute fields for the picked category (registry-sourced).
  void registryReady;
  const requiredFields: CategoryField[] = useMemo(() => {
    if (!formCategory) return [];
    const picker = CANONICAL_TO_PICKER[formCategory] || formCategory;
    return getCategoryFields(picker).filter((f) => f.required);
  }, [formCategory, registryReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fields the dry-run flagged (amber ring on the matching inputs).
  const gapFields = useMemo(
    () => new Set((dry?.gaps || []).map((g) => g.field).filter(Boolean) as string[]),
    [dry]
  );

  // ---- Save (review mini-form) → PUT /catalog/products/{id} ---------------
  const handleSave = useCallback(async () => {
    if (!id) return;
    const payload: UpdateCatalogProductPayload = {};
    const docCategory = canonicalCategory(str(doc.category));
    if (formCategory && formCategory !== docCategory) payload.category = formCategory;

    const attrPatch: Record<string, unknown> = {};
    Object.entries(formAttrs).forEach(([k, v]) => {
      const before = str(attributes[k]);
      if (v.trim() !== before.trim()) attrPatch[k] = v.trim();
    });
    if (Object.keys(attrPatch).length > 0) payload.attributes = attrPatch;

    const mrpNum = parseFloat(formMrp);
    const offerNum = parseFloat(formOffer);
    const pricing: NonNullable<UpdateCatalogProductPayload['pricing']> = {};
    if (Number.isFinite(mrpNum) && mrpNum !== docMrp(doc)) pricing.mrp = mrpNum;
    if (Number.isFinite(offerNum) && offerNum !== docOffer(doc)) pricing.offer_price = offerNum;
    if (Object.keys(pricing).length > 0) payload.pricing = pricing;

    // Only send HSN/GST when explicitly edited — an untouched pair lets a
    // category change re-derive both server-side.
    if (taxTouched) {
      if (formHsn.trim() && formHsn.trim() !== str(doc.hsn_code)) {
        payload.hsn_code = formHsn.trim();
      }
      const gstNum = parseFloat(formGst);
      if (Number.isFinite(gstNum) && String(gstNum) !== str(doc.gst_rate)) {
        payload.gst_rate = gstNum;
      }
    }

    if (Object.keys(payload).length === 0) {
      toast.info('Nothing changed yet.');
      return;
    }
    setSaving(true);
    try {
      await catalogProductsApi.update(id, payload);
      const fresh = await catalogProductsApi.get(id);
      setDoc(fresh as unknown as Record<string, unknown>);
      seedForm(fresh as unknown as Record<string, unknown>);
      onUpdated?.(fresh);
      toast.success('Saved — re-checking readiness…');
      void runDryRun(id);
    } catch (e: unknown) {
      toast.error(e instanceof Error && e.message ? e.message : 'Could not save the fixes.');
    } finally {
      setSaving(false);
    }
  }, [
    id, doc, attributes, formCategory, formAttrs, formMrp, formOffer, formHsn,
    formGst, taxTouched, onUpdated, runDryRun, seedForm, toast,
  ]);

  // ---- Approve for POS ------------------------------------------------------
  const handleApprove = useCallback(async () => {
    if (!id || approving) return;
    setApproving(true);
    try {
      const res = await catalogProductsApi.promote(id);
      onApproved?.(id, res.sku);
    } catch (e: unknown) {
      toast.error(
        e instanceof Error && e.message ? e.message : 'Approval failed — see the checklist.'
      );
      void runDryRun(id);
    } finally {
      setApproving(false);
    }
  }, [id, approving, onApproved, runDryRun, toast]);

  // ---- Archive / restore ----------------------------------------------------
  const handleArchiveToggle = useCallback(async () => {
    if (!id || archiving) return;
    setArchiving(true);
    try {
      await catalogProductsApi.update(id, { is_active: !inactive });
      const fresh = await catalogProductsApi.get(id);
      setDoc(fresh as unknown as Record<string, unknown>);
      onUpdated?.(fresh);
      toast.success(inactive ? 'Restored to the review queue.' : 'Archived — not for POS.');
    } catch (e: unknown) {
      toast.error(e instanceof Error && e.message ? e.message : 'Could not update the product.');
    } finally {
      setArchiving(false);
    }
  }, [id, archiving, inactive, onUpdated, toast]);

  // ---- "Edit everything" → the FULL cataloguing page (?review=<id>) --------
  // The parent (Catalog Manager) stashes the whole grid page as the queue;
  // standalone use falls back to a single-item stash so Prev/Next degrade
  // gracefully instead of erroring.
  const handleEditEverything = useCallback(() => {
    if (!id) return;
    if (onEditEverything) {
      onEditEverything(id);
      return;
    }
    writeReviewQueue({ ids: [id], index: 0 });
    navigate(`/catalog/add?review=${encodeURIComponent(id)}`);
  }, [id, onEditEverything, navigate]);

  // ---- Keyboard: Esc close, arrows prev/next (not while lightbox/input) ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (lightboxAt !== null) return; // the lightbox owns the keyboard
      const target = e.target as HTMLElement | null;
      const tag = (target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft' && onPrev) onPrev();
      else if (e.key === 'ArrowRight' && onNext) onNext();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxAt, onClose, onPrev, onNext]);

  const mrp = docMrp(doc);
  const offer = docOffer(doc);
  const hasDiscount = mrp !== null && offer !== null && offer < mrp;

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label={name}>
      {/* Backdrop — click closes, grid scroll/filters stay intact behind. */}
      <button
        type="button"
        aria-label="Close product details"
        className="absolute inset-0 bg-black/30 cursor-default"
        onClick={onClose}
      />

      <div className="absolute right-0 top-0 h-full w-full max-w-md bg-white shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-200">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {isImported && needsReview ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                  <AlertTriangle className="h-3 w-3" /> Needs review
                </span>
              ) : inactive ? (
                <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">
                  Inactive
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-medium text-green-700">
                  <ShieldCheck className="h-3 w-3" /> POS-ready
                </span>
              )}
              {str(doc.source) === 'bvi_import' && (
                <span className="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-600">
                  Imported
                </span>
              )}
            </div>
            <h2 className="mt-1 text-base font-semibold text-gray-900 truncate" title={name}>
              {name}
            </h2>
            <p className="text-xs text-gray-500 truncate">
              {categoryBrowseLabel(str(doc.category)) || '—'}
              {str(doc.sku) ? ` · ${str(doc.sku)}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={onPrev}
              disabled={!onPrev}
              className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-30"
              aria-label="Previous product"
              title="Previous (←)"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={onNext}
              disabled={!onNext}
              className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-30"
              aria-label="Next product"
              title="Next (→)"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Image strip */}
          {images.length > 0 ? (
            <div className="flex gap-2 overflow-x-auto pb-1">
              {images.map((url, i) => (
                <button
                  key={`${url}-${i}`}
                  type="button"
                  onClick={() => setLightboxAt(i)}
                  className="h-24 w-24 shrink-0 rounded-lg border border-gray-200 bg-white flex items-center justify-center overflow-hidden"
                  aria-label={`View image ${i + 1}`}
                >
                  <CatalogImage url={url} alt={name} className="h-full w-full object-contain" />
                </button>
              ))}
            </div>
          ) : (
            <div className="h-24 rounded-lg border border-dashed border-gray-200 flex items-center justify-center text-gray-300">
              <Package className="w-8 h-8" />
            </div>
          )}

          {/* Pricing headline */}
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-lg font-bold text-gray-900">{fmtINR(offer ?? mrp)}</span>
            {hasDiscount && (
              <span className="text-sm text-gray-400 line-through">{fmtINR(mrp)}</span>
            )}
          </div>

          {/* Identity */}
          <GroupTitle>Identity</GroupTitle>
          <dl className="divide-y divide-gray-50">
            <FactRow
              label="Brand"
              value={str(doc.brand || attributes.brand_name)}
            />
            <FactRow label="Name" value={name} />
            <FactRow label="SKU" value={str(doc.sku)} />
            <FactRow label="Barcode" value={str(doc.barcode)} />
            <FactRow
              label="Category"
              value={
                <>
                  {categoryBrowseLabel(str(doc.category))}
                  {doc.category_unmapped === true && (
                    <span className="ml-1.5 inline-flex items-center rounded bg-amber-100 px-1 py-px text-[10px] text-amber-700">
                      unmapped
                    </span>
                  )}
                </>
              }
            />
          </dl>

          {/* Pricing */}
          <GroupTitle>Pricing</GroupTitle>
          <dl className="divide-y divide-gray-50">
            <FactRow label="MRP" value={fmtINR(mrp)} />
            <FactRow label="Offer price" value={fmtINR(offer)} />
            <FactRow
              label="HSN / GST"
              value={`${str(doc.hsn_code) || '—'} · ${str(doc.gst_rate) || '—'}%`}
            />
          </dl>

          {/* Attributes */}
          {Object.keys(attributes).length > 0 && (
            <>
              <GroupTitle>Attributes</GroupTitle>
              <dl className="divide-y divide-gray-50">
                {Object.entries(attributes).map(([k, v]) =>
                  str(v) ? <FactRow key={k} label={humanKey(k)} value={str(v)} /> : null
                )}
              </dl>
            </>
          )}

          {/* Tags */}
          {Array.isArray(doc.tags) && (doc.tags as unknown[]).length > 0 && (
            <>
              <GroupTitle>Tags</GroupTitle>
              <div className="flex flex-wrap gap-1.5">
                {(doc.tags as unknown[]).map((t, i) => (
                  <span
                    key={`${str(t)}-${i}`}
                    className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600"
                  >
                    {str(t)}
                  </span>
                ))}
              </div>
            </>
          )}

          {/* Online / ecom status */}
          {ecom && (
            <>
              <GroupTitle>Online store</GroupTitle>
              <dl className="divide-y divide-gray-50">
                <FactRow label="Status" value={str(ecom.status)} />
                <FactRow
                  label="Page"
                  value={
                    str(ecom.page_url) ? (
                      <a
                        href={str(ecom.page_url)}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-blue-600 hover:underline"
                      >
                        <Globe className="w-3 h-3" /> {str(ecom.handle) || 'view'}
                      </a>
                    ) : (
                      str(ecom.handle)
                    )
                  }
                />
                <FactRow label="Shopify id" value={str(ecom.shopify_product_id)} />
              </dl>
            </>
          )}

          {/* ------------------------ REVIEW PANEL ------------------------ */}
          {isImported && (
            <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50/50 p-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                  Review for POS
                  {dryLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-400" />}
                </h3>
                {/* THE full-page door: every field editable, not just the
                    quick-fix set below. */}
                <button
                  type="button"
                  onClick={handleEditEverything}
                  className="btn-primary !py-1.5 !px-3 text-xs flex items-center gap-1.5 shrink-0"
                  title="Open the full cataloguing page — every field editable"
                >
                  <Pencil className="w-3.5 h-3.5" /> Edit everything
                </button>
              </div>

              {/* Checklist */}
              <div className="mt-2 space-y-1">
                {dry === null && dryLoading && (
                  <p className="text-xs text-gray-500">Checking what's missing…</p>
                )}
                {dry?.ok && (
                  <p className="flex items-center gap-1.5 text-xs text-green-700">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Everything checks out — ready to
                    approve.
                  </p>
                )}
                {dry && !dry.ok &&
                  dry.gaps.map((g, i) => (
                    <p key={`${g.field || 'gap'}-${i}`} className="flex items-start gap-1.5 text-xs text-amber-800">
                      <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                      <span>{g.message}</span>
                    </p>
                  ))}
                {(dry?.duplicate_warnings || []).map((w, i) => (
                  <p key={`dup-${i}`} className="flex items-start gap-1.5 text-xs text-orange-700">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                    <span>
                      Possible duplicate ({w.reason}): {[w.brand, w.model].filter(Boolean).join(' ') || w.name || w.sku}
                      {w.sku ? ` (SKU ${w.sku})` : ''} — approving creates a second sellable
                      product.
                    </span>
                  </p>
                ))}
              </div>

              {/* Quick-fix mini-form */}
              <div className="mt-3 grid grid-cols-2 gap-2.5">
                <div className="col-span-2">
                  <label className="block text-[11px] font-medium text-gray-700 mb-0.5">
                    Category
                  </label>
                  <select
                    value={formCategory}
                    onChange={(e) => setFormCategory(e.target.value)}
                    className="input-field w-full"
                    title="Category"
                  >
                    <option value="">Select category</option>
                    {CATEGORY_BROWSE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    Changing the category re-derives HSN &amp; GST automatically unless you set
                    them below.
                  </p>
                </div>

                {requiredFields.map((f) => (
                  <div key={f.name} className={clsx(f.name === 'brand_name' && 'col-span-2')}>
                    <label className="block text-[11px] font-medium text-gray-700 mb-0.5">
                      {f.label} <span className="text-red-500">*</span>
                    </label>
                    {f.type === 'select' && Array.isArray(f.options) ? (
                      <select
                        value={formAttrs[f.name] || ''}
                        onChange={(e) =>
                          setFormAttrs((prev) => ({ ...prev, [f.name]: e.target.value }))
                        }
                        title={f.label}
                        className={clsx(
                          'input-field w-full',
                          gapFields.has(f.name) && 'ring-1 ring-amber-400 bg-amber-50/60'
                        )}
                      >
                        <option value="">Select {f.label}</option>
                        {/* Keep a legacy value visible even if it's not in the list. */}
                        {formAttrs[f.name] &&
                          !f.options.some(
                            (o) => o.toLowerCase() === (formAttrs[f.name] || '').toLowerCase()
                          ) && <option value={formAttrs[f.name]}>{formAttrs[f.name]}</option>}
                        {f.options.map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'}
                        value={formAttrs[f.name] || ''}
                        onChange={(e) =>
                          setFormAttrs((prev) => ({ ...prev, [f.name]: e.target.value }))
                        }
                        placeholder={f.placeholder || f.label}
                        title={f.label}
                        className={clsx(
                          'input-field w-full',
                          gapFields.has(f.name) && 'ring-1 ring-amber-400 bg-amber-50/60'
                        )}
                      />
                    )}
                  </div>
                ))}

                <div>
                  <label className="block text-[11px] font-medium text-gray-700 mb-0.5">
                    MRP (₹)
                  </label>
                  <input
                    type="number"
                    value={formMrp}
                    onChange={(e) => setFormMrp(e.target.value)}
                    title="MRP"
                    className={clsx(
                      'input-field w-full',
                      gapFields.has('mrp') && 'ring-1 ring-amber-400 bg-amber-50/60'
                    )}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-700 mb-0.5">
                    Offer price (₹)
                  </label>
                  <input
                    type="number"
                    value={formOffer}
                    onChange={(e) => setFormOffer(e.target.value)}
                    title="Offer price"
                    className={clsx(
                      'input-field w-full',
                      gapFields.has('offer_price') && 'ring-1 ring-amber-400 bg-amber-50/60'
                    )}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-700 mb-0.5">HSN</label>
                  <input
                    type="text"
                    value={formHsn}
                    onChange={(e) => {
                      setFormHsn(e.target.value);
                      setTaxTouched(true);
                    }}
                    title="HSN code"
                    className="input-field w-full"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-700 mb-0.5">
                    GST %
                  </label>
                  <input
                    type="number"
                    value={formGst}
                    onChange={(e) => {
                      setFormGst(e.target.value);
                      setTaxTouched(true);
                    }}
                    title="GST rate"
                    className="input-field w-full"
                  />
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="btn-secondary flex items-center gap-1.5 text-sm"
                >
                  {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  Save fixes
                </button>
                <button
                  type="button"
                  onClick={() => void handleApprove()}
                  disabled={!dry?.ok || approving || dryLoading}
                  className={clsx(
                    'flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-white',
                    dry?.ok && !approving
                      ? 'bg-green-600 hover:bg-green-700'
                      : 'bg-gray-300 cursor-not-allowed'
                  )}
                  title={dry?.ok ? 'Create the billing record and make it sellable' : 'Fix the checklist first'}
                >
                  {approving ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  )}
                  Approve for POS
                </button>
                <button
                  type="button"
                  onClick={() => void handleArchiveToggle()}
                  disabled={archiving}
                  className="ml-auto flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-gray-500 hover:bg-gray-100"
                  title={
                    inactive
                      ? 'Return this item to the review queue'
                      : 'Not for POS — hide it from the active queue'
                  }
                >
                  {inactive ? (
                    <ArchiveRestore className="w-3.5 h-3.5" />
                  ) : (
                    <Archive className="w-3.5 h-3.5" />
                  )}
                  {inactive ? 'Restore' : 'Archive'}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer actions (spine products) */}
        {!isImported && (
          <div className="border-t border-gray-200 px-5 py-3 flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate(`/catalog/add?edit=${encodeURIComponent(id)}`)}
              className="btn-primary flex items-center gap-1.5 text-sm"
            >
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
            <button
              type="button"
              onClick={() => navigate(`/catalog/add?clone=${encodeURIComponent(id)}`)}
              className="btn-secondary flex items-center gap-1.5 text-sm"
              title="Duplicate as a new SKU"
            >
              <CopyPlus className="w-3.5 h-3.5" /> Clone
            </button>
            <button
              type="button"
              onClick={() => navigate(`/catalog/buy-desk?add_product=${encodeURIComponent(id)}`)}
              className="ml-auto flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
            >
              <ShoppingCart className="w-3.5 h-3.5" /> Order stock
            </button>
          </div>
        )}
      </div>

      {lightboxAt !== null && images.length > 0 && (
        <ImageLightbox
          images={images}
          startIndex={lightboxAt}
          alt={name}
          onClose={() => setLightboxAt(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lazy, referrer-stripped product image with the Package-icon fallback (BVI
// /uploads URLs may 404 until the rehost pass runs). Module-level component.
// ---------------------------------------------------------------------------
export function CatalogImage({
  url,
  alt,
  className,
}: {
  url: string;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  // Reset when the url changes (drawer prev/next re-uses the element).
  useEffect(() => setFailed(false), [url]);
  if (!url || failed) {
    return (
      <span className="flex h-full w-full items-center justify-center text-gray-300">
        <Package className="w-8 h-8" />
      </span>
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      className={className}
      onError={() => setFailed(true)}
    />
  );
}

export default CatalogProductDrawer;
