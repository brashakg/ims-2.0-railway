// ============================================================================
// Quick Add - the form itself: state, validation, submit and the loaders.
// ============================================================================
// MOVED verbatim out of QuickAddPage.tsx by the Wave 3 file diet. Nothing in
// here is new or rewritten: the same state, the same effects in the same order,
// the same handlers with the same dependency arrays. QuickAddPage now calls
// this once and hands the returned object to each block, so there is still
// exactly ONE copy of the form state - the blocks read it, they never own it.
//
// The image machinery lives next door in useProductImages (its return is
// spread into this one, so the blocks see the same flat names as before).

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import {
  productApi,
  DuplicateProductError,
  type DuplicateProductInfo,
} from '../../../services/api/products';
// Import the templates service DIRECTLY from its module (not the api barrel —
// the barrel re-export fails to resolve for new services, TS2614).
import { productTemplatesApi, type ProductTemplate } from '../../../services/api/productTemplates';
// Catalog-products (imported review docs) API — DIRECT module import (TS2614).
import {
  catalogProductsApi,
  CatalogRequestError,
  type CatalogProductDoc,
  type PromoteDryRunResult,
} from '../../../services/api/catalog';
import {
  buildProductPayload,
  catalogDocToFormValues,
  dictionaryErrorField,
  fieldLabelFor,
  formValuesToCatalogUpdate,
  hsnImpliesCategoryRate,
  loadCategoryRegistry,
  overlayChangedFormValues,
  productToFormValues,
  productToVariantFormValues,
  promoteGapsToFormErrors,
  resolveHsnGst,
  type ProductDoc,
  type ProductFormValues,
  validateProductForm,
  validateReviewForm,
  variantFieldRule,
  variantFlaggedFormFields,
} from '../productAddShared';
import {
  readReviewQueue,
  writeReviewQueue,
  removeFromReviewQueue,
} from '../reviewQueue';
import { productListPath, sectionOfError, type EditMode, type SectionId } from './shared';
import { useProductImages } from './useProductImages';

export function useQuickAddForm() {
  const { hasRole, user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // ---- Form state (mirrors the wizard's fields exactly) --------------------
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const [description, setDescription] = useState('');
  const [hsnCode, setHsnCode] = useState('');
  const [gstRate, setGstRate] = useState('18');
  const [weight, setWeight] = useState('');

  // Pricing
  const [mrp, setMrp] = useState('');
  const [offerPrice, setOfferPrice] = useState('');
  const [costPrice, setCostPrice] = useState('');
  const [discountCategory, setDiscountCategory] = useState('');

  // Inventory. Stock is added via Goods Receipt (GRN), and both the SKU and our
  // internal barcode are auto-assigned (SKU at create, barcode at GRN) — there
  // is no manual quantity or barcode entry here. Only the reorder level is set.
  const [reorderLevel, setReorderLevel] = useState('5');

  // Online (Shopify)
  const [syncToShopify, setSyncToShopify] = useState(false);
  const [shopifyTags, setShopifyTags] = useState<string[]>([]);
  const [publishPOS, setPublishPOS] = useState(true);

  // Product images (Part 1): self-hosted URLs returned by the upload endpoint.
  const [images, setImages] = useState<string[]>([]);

  // UI state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // HSN / GST / weight open by default: they auto-fill from the category, so the
  // operator sees + can tweak them without the extra "Advanced" click.
  const [showAdvanced, setShowAdvanced] = useState(true);
  const [openSections, setOpenSections] = useState<Record<SectionId, boolean>>({
    identity: true,
    pricing: true,
    inventory: true,
  });

  // ---- Templates + clone state (Phase C) -----------------------------------
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [templates, setTemplates] = useState<ProductTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [cloneSku, setCloneSku] = useState('');
  const [cloning, setCloning] = useState(false);

  // ---- Duplicate rescue + variant mode (Phase 1) ----------------------------
  // dupInfo != null -> the rescue popup is open (the form behind stays fully
  // intact). variantCtx != null -> the form is in VARIANT MODE: adding a new
  // colour/size of an existing model, with brand/model locked, the copied
  // sizes + prices amber-flagged until touched, and a save STREAK that keeps
  // the model-level fields for the next sibling.
  const [dupInfo, setDupInfo] = useState<DuplicateProductInfo | null>(null);
  const [dupBusy, setDupBusy] = useState(false);
  const [variantCtx, setVariantCtx] = useState<{
    sourceProductId: string;
    sourceSku: string;
    sourceLabel: string;
    locked: Set<string>;
    sourceImages: string[];
    firstClearedField: string | null;
    dictionaryNote: string;
  } | null>(null);
  // Copied-but-unconfirmed fields (attribute names + form-level keys like
  // 'mrp'/'offer_price'). Amber ring until the operator touches the field.
  const [flaggedFields, setFlaggedFields] = useState<Set<string>>(new Set());

  // ---- EDIT modes (Catalog Manager drawer -> ?edit=<id> / ?review=<id>) ----
  // kind='spine' (was `editingId`): handleSubmit issues ONE PUT /products/{id}
  // instead of a create: SKU/barcode/category are identity (read-only),
  // reorder_point rides inside the same PUT, and the dup-rescue popup can't
  // trigger (only createProduct throws DuplicateProductError). Success returns
  // the owner to the Catalog Manager with the drawer re-opened (?focus=<id>).
  // kind='catalog': the full-page review editor over an imported doc — its own
  // submit fork (handleReviewSave/handleReviewApprove) + the ReviewQueueBar.
  const [editMode, setEditMode] = useState<EditMode>(null);
  const isReviewMode = editMode?.kind === 'catalog';

  // ---- Review-mode state (?review=<id>) -------------------------------------
  // The loaded doc + the form values as loaded/last-saved — the diff base for
  // the save payload and the dirty check.
  const [reviewSnapshot, setReviewSnapshot] = useState<{
    doc: CatalogProductDoc;
    values: ProductFormValues;
  } | null>(null);
  const [reviewDry, setReviewDry] = useState<PromoteDryRunResult | null>(null);
  const [reviewDryLoading, setReviewDryLoading] = useState(false);
  const [reviewApproving, setReviewApproving] = useState(false);
  // Queue position ("Item N of M") from the sessionStorage stash; null when
  // deep-linked with no stash context.
  const [queuePos, setQueuePos] = useState<{ n: number; m: number; hasPrev: boolean } | null>(
    null
  );
  // Terminal state: nothing left to review (friendly panel, never an error).
  const [queueClear, setQueueClear] = useState(false);
  // The CURRENT ?review id, kept in sync synchronously by the loader effect.
  // Post-await continuations (save/approve) check it so a slow response for
  // item A can never stomp item B's form/snapshot after Alt+Arrow navigation.
  const reviewIdRef = useRef<string | null>(null);
  // Review-only editable fields: display name (PUT `name` -> name+title) and
  // governed tags (PUT `tags`).
  const [displayName, setDisplayName] = useState('');
  const [reviewTags, setReviewTags] = useState<string[]>([]);

  const firstFieldRef = useRef<HTMLSelectElement | HTMLInputElement | null>(null);
  // Bump on registry load so the field list (required markers sourced from the
  // canonical server registry) re-renders once it arrives.
  const [registryReady, setRegistryReady] = useState(false);
  // Brand Master projection for the selected category: brand name -> its
  // sub-brand names. Drives the per-brand Sub Brand select (a brand with no
  // sub-brands keeps the field free-form). Fail-soft {}.
  const [subbrandsByBrand, setSubbrandsByBrand] = useState<Record<string, string[]>>({});
  // Brand name -> Brand Master tier (MASS/PREMIUM/LUXURY). The discount band
  // is no longer picked per product — the backend derives it from this tier
  // (category force wins); shown read-only in the Review.
  const [brandTiers, setBrandTiers] = useState<Record<string, string>>({});

  // Load the canonical category field registry once (shared module cache). The
  // required/optional flags the form renders + validates derive from it so they
  // match the server create gate. Fail-soft: a fetch error leaves the local
  // CATEGORY_FIELDS fallback flags in place.
  useEffect(() => {
    let alive = true;
    loadCategoryRegistry()
      .then(() => { if (alive) setRegistryReady(true); })
      .catch(() => { /* fall back to local required flags */ });
    return () => { alive = false; };
  }, []);

  // When a template/clone is loaded we set category AND an explicit HSN/GST.
  // This flag tells the category-change autofill below to skip exactly one
  // cycle so the loaded HSN/GST (which may be a 6-digit / overridden value)
  // isn't immediately clobbered by the category default.
  const skipHsnAutofillRef = useRef(false);

  // Auto-fill HSN + GST when category (or 4/6-digit toggle) changes — same
  // behaviour as the wizard's useEffect. REVIEW MODE applies NO local HSN/GST
  // overwrite on a category change: the diff-only save omits the untouched
  // pair, so the SERVER's category-change re-derivation wins (a hint chip by
  // the picker says so); the re-derived values re-seed from the PUT response.
  useEffect(() => {
    if (skipHsnAutofillRef.current) {
      skipHsnAutofillRef.current = false;
      return;
    }
    if (isReviewMode) return;
    if (selectedCategory) {
      const { hsnCode: hc, gstRate: gr } = resolveHsnGst(selectedCategory);
      if (hc) setHsnCode(hc);
      setGstRate(gr);
    }
  }, [selectedCategory, isReviewMode]);

  // Does the HSN on the form still imply the rate the form is showing?
  // The rule itself lives in productAddShared.hsnImpliesCategoryRate, where it
  // can be tested without standing this whole page up.
  const hsnMatchesCategory = useMemo(
    () => hsnImpliesCategoryRate(selectedCategory, hsnCode),
    [hsnCode, selectedCategory],
  );

  // Keyboard-first: when a category is picked, move focus to the first
  // category field so the user can start typing without reaching for the mouse.
  useEffect(() => {
    if (!selectedCategory) return;
    const t = window.setTimeout(() => firstFieldRef.current?.focus(), 60);
    return () => window.clearTimeout(t);
  }, [selectedCategory]);

  // Load the Brand Master projection (brand -> sub-brands) for the selected
  // category so the Sub Brand field can restrict to the chosen brand's
  // sub-brands. Fail-soft: any error just leaves subbrand free-form.
  useEffect(() => {
    if (!selectedCategory) {
      setSubbrandsByBrand({});
      setBrandTiers({});
      return;
    }
    let alive = true;
    productApi
      .getBrandOptions(selectedCategory)
      .then((r) => {
        if (!alive) return;
        const map: Record<string, string[]> = {};
        const tiers: Record<string, string> = {};
        (r.brands || []).forEach((b) => {
          if (b?.name) {
            map[b.name] = Array.isArray(b.subbrands) ? b.subbrands : [];
            if (b.tier) tiers[b.name] = b.tier;
          }
        });
        setSubbrandsByBrand(map);
        setBrandTiers(tiers);
      })
      .catch(() => { /* free-form fallback */ });
    return () => { alive = false; };
  }, [selectedCategory]);

  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  // F35: cost price + margin are visible only to cost-authorised roles (matches
  // the Guided wizard). CATALOG_MANAGER may set cost on this product form.
  const canSeeCost = hasRole(['SUPERADMIN', 'ADMIN', 'ACCOUNTANT', 'CATALOG_MANAGER']);

  const currentValues = useCallback(
    (): ProductFormValues => ({
      category: selectedCategory,
      attributes,
      description,
      hsnCode,
      gstRate,
      weight,
      mrp,
      offerPrice,
      costPrice,
      discountCategory,
      syncToShopify,
      shopifyTags,
      publishPOS,
      images,
      // Review-mode extras (ignored by buildProductPayload / create doors).
      name: displayName,
      tags: reviewTags,
    }),
    [
      selectedCategory, attributes, description, hsnCode, gstRate, weight, mrp,
      offerPrice, costPrice, discountCategory, syncToShopify, shopifyTags, publishPOS,
      images, displayName, reviewTags,
    ]
  );

  // Reset the form. `keepIdentity` (used by Save + New) keeps category + brand
  // so the next variant of the same product is fast to enter.
  const resetForm = useCallback(
    (keepIdentity: boolean) => {
      const keptBrand = attributes.brand_name;
      setAttributes(keepIdentity && keptBrand ? { brand_name: keptBrand } : {});
      if (!keepIdentity) setSelectedCategory('');
      setDescription('');
      setWeight('');
      setMrp('');
      setOfferPrice('');
      setCostPrice('');
      setDiscountCategory('');
      setReorderLevel('5');
      setSyncToShopify(false);
      setShopifyTags([]);
      setPublishPOS(true);
      setImages([]);
      setErrors({});
      setDisplayName('');
      setReviewTags([]);
    },
    [attributes.brand_name]
  );

  // Apply a ProductFormValues blob to every form field. The inverse of
  // currentValues(); used by BOTH "load template" and "clone product" so the
  // two prefill paths stay identical. Does NOT touch the SKU/barcode/qty
  // (inventory) fields — a loaded shape is a starting point, not a real SKU.
  const applyFormValues = useCallback((v: ProductFormValues) => {
    // Preserve the loaded HSN/GST: skip the next category-driven autofill so a
    // saved 6-digit / overridden HSN survives. If the blob has no HSN, let the
    // autofill run so the category default still fills in.
    if (v.hsnCode) skipHsnAutofillRef.current = true;
    setSelectedCategory(v.category || '');
    setAttributes(v.attributes || {});
    setDescription(v.description || '');
    setHsnCode(v.hsnCode || '');
    setGstRate(v.gstRate || '18');
    setWeight(v.weight || '');
    setMrp(v.mrp || '');
    setOfferPrice(v.offerPrice || '');
    setCostPrice(v.costPrice || '');
    setDiscountCategory(v.discountCategory || '');
    setSyncToShopify(Boolean(v.syncToShopify));
    setShopifyTags(Array.isArray(v.shopifyTags) ? v.shopifyTags : []);
    setPublishPOS(v.publishPOS !== false);
    setImages(Array.isArray(v.images) ? v.images : []);
    // Review extras — blank for every non-review prefill (template/clone/
    // variant all leave them undefined).
    setDisplayName(v.name || '');
    setReviewTags(Array.isArray(v.tags) ? v.tags : []);
    setErrors({});
    // Reveal Inventory too when a prefill carries images so they're visible.
    setOpenSections((s) => ({
      ...s,
      identity: true,
      pricing: true,
      ...(Array.isArray(v.images) && v.images.length > 0 ? { inventory: true } : {}),
    }));
  }, []);

  // ---- Variant mode (Phase 1) ----------------------------------------------
  // Focus a specific attribute input by field name (each input carries
  // id="qa-field-<name>"). Used to land the cursor on the first CLEARED
  // variant field when entering variant mode / starting the next streak item.
  const focusAttrField = useCallback((name: string | null) => {
    if (!name) return;
    window.setTimeout(() => {
      document.getElementById(`qa-field-${name}`)?.focus();
    }, 120);
  }, []);

  // "Still missing" chip / section count -> the field itself. Opens the
  // accordion (and the Advanced row for the HSN) first, because a closed
  // section is unmounted and there would be nothing to scroll to.
  const jumpToField = useCallback((name: string) => {
    setOpenSections((s) => ({ ...s, [sectionOfError(name)]: true }));
    if (name === 'hsn_code') setShowAdvanced(true);
    window.setTimeout(() => {
      const el = document.getElementById(`qa-field-${name}`);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.focus({ preventScroll: true });
    }, 120);
  }, []);

  // Drop a field's amber "confirm" flag once the operator touches it.
  const clearFlag = useCallback((name: string) => {
    setFlaggedFields((prev) => {
      if (!prev.has(name)) return prev;
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  }, []);

  // Flip the form into VARIANT MODE seeded from an existing product. Shared by
  // the duplicate-rescue popup's default action and the ?variant=<id> deep
  // link (the "+ Variant" button in the product list emits that URL).
  const enterVariantMode = useCallback(
    (product: ProductDoc) => {
      const seed = productToVariantFormValues(product);
      if (!seed.category) {
        toast.error("Couldn't resolve this product's category to start a variant.");
        return;
      }
      applyFormValues(seed.values);
      setVariantCtx({
        sourceProductId: seed.sourceProductId,
        sourceSku: seed.sourceSku,
        sourceLabel: seed.sourceLabel || seed.sourceSku || 'this model',
        locked: new Set(seed.locked),
        sourceImages: seed.sourceImages,
        firstClearedField: seed.cleared[0] || null,
        dictionaryNote:
          seed.dictionaryDropped.length > 0
            ? `${seed.dictionaryDropped.length} copied value${
                seed.dictionaryDropped.length === 1 ? " isn't" : "s aren't"
              } in your dictionary — pick from the dropdown (${seed.dictionaryDropped
                .map((k) => k.replace(/_/g, ' '))
                .join(', ')}).`
            : '',
      });
      setFlaggedFields(new Set(seed.flagged));
      focusAttrField(seed.cleared[0] || null);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [applyFormValues, focusAttrField, toast]
  );

  // Leave variant mode -> a fresh blank form ("New model" button / Esc).
  const exitVariantMode = useCallback(() => {
    setVariantCtx(null);
    setFlaggedFields(new Set());
    resetForm(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [resetForm]);

  // VARIANT STREAK: after a successful save in variant mode, stay in variant
  // mode — keep the model-level (copy/flag) fields, clear the variant-defining
  // ones, re-arm the amber confirmations, focus back on the first variant
  // field. Esc / "New model" exits to a blank form.
  const startNextVariant = useCallback(() => {
    const keptAttrs: Record<string, string> = {};
    const attrFlags: string[] = [];
    Object.entries(attributes).forEach(([k, v]) => {
      if (!String(v ?? '').trim()) return;
      const rule = variantFieldRule(selectedCategory, k);
      if (rule === 'copy' || rule === 'flag') {
        keptAttrs[k] = v;
        if (rule === 'flag') attrFlags.push(k);
      }
    });
    setAttributes(keptAttrs);
    setDescription('');
    setImages([]);
    setErrors({});
    setFlaggedFields(
      new Set([...attrFlags, ...variantFlaggedFormFields(currentValues())])
    );
    focusAttrField(variantCtx?.firstClearedField ?? null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [attributes, selectedCategory, currentValues, focusAttrField, variantCtx]);

  // Rescue popup: default action — fetch the existing product and flip into
  // variant mode seeded from it. The enriched 409 payload carries product_id.
  const handleDupAddVariant = useCallback(async () => {
    const pid = dupInfo?.product_id;
    if (!pid) {
      setDupInfo(null);
      toast.error("Couldn't identify the existing product — search it in Inventory.");
      return;
    }
    setDupBusy(true);
    try {
      const product = (await productApi.getProduct(pid)) as ProductDoc;
      setDupInfo(null);
      enterVariantMode(product);
    } catch {
      toast.error('Could not load the existing product for a variant.');
    } finally {
      setDupBusy(false);
    }
  }, [dupInfo, enterVariantMode, toast]);

  // Rescue popup: open the existing product in the stock ledger (see
  // productListPath) pre-scoped to its SKU.
  const handleDupOpenExisting = useCallback(() => {
    const sku = dupInfo?.sku;
    setDupInfo(null);
    navigate(productListPath(sku));
  }, [dupInfo, navigate]);

  // ---- Similar-products strip (Phase 2) -------------------------------------
  // A sibling chip prefills the form through the SAME Phase 1 variant path the
  // rescue popup uses (fetch the product -> enterVariantMode); the exact-match
  // "Open it" follows the same product-open destination as the popup.
  const handleSimilarPick = useCallback(
    async (productId: string) => {
      if (!productId) return;
      try {
        const product = (await productApi.getProduct(productId)) as ProductDoc;
        enterVariantMode(product);
      } catch {
        toast.error('Could not load that product to start a variant.');
      }
    },
    [enterVariantMode, toast]
  );

  const handleSimilarOpen = useCallback(
    (sku?: string | null) => {
      navigate(productListPath(sku));
    },
    [navigate]
  );

  // ---- Product images (Part 1) ---------------------------------------------
  // Upload / remove / remove-background, unchanged, in their own module.
  const imageCtl = useProductImages(setImages);


  const handleSubmit = useCallback(
    async (saveAndNew: boolean) => {
      const values = currentValues();
      const newErrors = validateProductForm(values);
      setErrors(newErrors);
      if (Object.keys(newErrors).length > 0) {
        // Make sure every section holding an error is open (a closed section
        // is unmounted, so its inline error would stay hidden).
        const keys = Object.keys(newErrors);
        setOpenSections((s) => ({
          ...s,
          ...(keys.some((k) => sectionOfError(k) === 'pricing') ? { pricing: true } : {}),
          ...(keys.some((k) => sectionOfError(k) === 'identity') ? { identity: true } : {}),
        }));
        if (newErrors.hsn_code) setShowAdvanced(true);
        toast.error('Please fix the highlighted fields.');
        return;
      }

      setIsSubmitting(true);
      try {
        if (editMode?.kind === 'spine') {
          // EDIT-IN-PLACE: one validated PUT. Identity (SKU/barcode/category)
          // is never sent — it is immutable through this door; reorder_point
          // rides inside the same PUT (no follow-up write), and the 409
          // dup-rescue branch can't fire (PUT never throws DuplicateProductError).
          // (Review mode never reaches handleSubmit — it has its own fork.)
          const payload = buildProductPayload(values);
          const reorderNum = Number(reorderLevel);
          await productApi.updateProduct(editMode.id, {
            brand: payload.brand,
            model: payload.model,
            attributes: payload.attributes,
            mrp: payload.mrp,
            offer_price: payload.offer_price,
            hsn_code: payload.hsn_code,
            gst_rate: payload.gst_rate,
            description: values.description || undefined,
            weight: payload.weight,
            cost_price: payload.cost_price,
            images: payload.images,
            ...(payload.discount_category
              ? { discount_category: payload.discount_category }
              : {}),
            ...(Number.isFinite(reorderNum) && reorderNum >= 0
              ? { reorder_point: reorderNum }
              : {}),
          });
          toast.success(
            editMode.sku ? `Updated ${editMode.sku} — same SKU, no new product.` : 'Product updated.'
          );
          navigate(`/catalog?focus=${encodeURIComponent(editMode.id)}`);
          return;
        }
        const created = await productApi.createProduct(buildProductPayload(values));
        // Persist the reorder level via a follow-up update on the new product_id
        // (ProductCreate doesn't model reorder_point; ProductUpdate does). The
        // SKU is auto-minted by the backend and our internal barcode is assigned
        // at Goods Receipt — neither is entered here. Fail-soft: a failed reorder
        // update must not fail the create the user just did.
        const newId = created?.product_id || created?.id;
        const reorderNum = Number(reorderLevel);
        if (newId && Number.isFinite(reorderNum) && reorderNum >= 0) {
          try {
            await productApi.updateProduct(newId, { reorder_point: reorderNum });
          } catch {
            toast.warning('Product created, but the reorder level could not be saved.');
          }
        }
        // Surface the auto-assigned SKU (and barcode, if the backend returned one)
        // so the operator sees the clean system-generated identifiers.
        const createdSku = created?.sku;
        const createdBarcode = (created as { barcode?: string } | undefined)?.barcode;
        toast.success(
          createdSku
            ? `Product created — SKU ${createdSku}${createdBarcode ? ` · barcode ${createdBarcode}` : ''}.`
            : 'Product created successfully!',
          8000,
          // Procurement Phase 1: one-click hop to the Buy Desk with the new
          // product preselected (?add_product= handled in BuyDeskPage).
          newId
            ? {
                label: 'Order this now',
                onClick: () => navigate(`/catalog/buy-desk?add_product=${encodeURIComponent(newId)}`),
              }
            : undefined
        );
        if (variantCtx) {
          // VARIANT STREAK: stay in variant mode for the next colour/size of
          // the same model (Esc / "New model" exits to a blank form).
          startNextVariant();
        } else if (saveAndNew) {
          resetForm(true);
          // Keep focus flowing — jump back to the top of the form.
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          navigate('/inventory');
        }
      } catch (err) {
        if (err instanceof DuplicateProductError) {
          // Duplicate hard-block (409): open the rescue popup with the
          // existing product. The form state stays FULLY intact behind it.
          setDupInfo(err.existing || {});
          return;
        }
        toast.error(
          err instanceof Error && err.message
            ? err.message
            : 'Failed to create product. Please try again.'
        );
      } finally {
        setIsSubmitting(false);
      }
    },
    [
      currentValues, toast, resetForm, navigate, variantCtx, startNextVariant,
      editMode, reorderLevel,
    ]
  );

  // ==========================================================================
  // REVIEW MODE (?review=<id>) — save / approve / queue navigation
  // ==========================================================================

  // Dirty = the diff-only PUT payload is non-empty (vs the loaded snapshot).
  const reviewDirty = useMemo(() => {
    if (editMode?.kind !== 'catalog' || !reviewSnapshot) return false;
    return Object.keys(formValuesToCatalogUpdate(currentValues(), reviewSnapshot.values)).length > 0;
  }, [editMode, reviewSnapshot, currentValues]);

  const runReviewDryRun = useCallback(async (id: string) => {
    setReviewDryLoading(true);
    try {
      const res = await catalogProductsApi.promoteDryRun(id);
      setReviewDry(res);
    } catch (e: unknown) {
      // A hard 409 (already approved / SKU clash) surfaces here as one row.
      setReviewDry({
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
      setReviewDryLoading(false);
    }
  }, []);

  // Advance = swap the ?review param in place (NO navigate/reload — the
  // loader effect below re-seeds the form for the new id).
  const openReviewItem = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          const sp = new URLSearchParams(prev);
          sp.set('review', id);
          return sp;
        },
        { replace: true }
      );
    },
    [setSearchParams]
  );

  // Move to the next queue item. 'remove' drops the current id first (it was
  // approved / no longer reviewable); 'skip' keeps it and steps past it. When
  // the stash is exhausted/stale, fall back to asking the server for the next
  // waiting item; nothing left = the friendly "Review queue clear" panel.
  const advanceReview = useCallback(
    async (mode: 'remove' | 'skip') => {
      if (editMode?.kind !== 'catalog') return;
      const currentId = editMode.id;
      const stash = readReviewQueue();
      let nextId: string | undefined;
      if (stash && stash.ids.includes(currentId)) {
        const at = stash.ids.indexOf(currentId);
        if (mode === 'remove') {
          const after = removeFromReviewQueue(currentId);
          nextId = after?.ids[at]; // the item that shifted into this slot
          if (after && nextId) writeReviewQueue({ ...after, index: at });
        } else {
          nextId = stash.ids[at + 1];
          if (nextId) writeReviewQueue({ ...stash, index: at + 1 });
        }
      }
      if (nextId) {
        openReviewItem(nextId);
        return;
      }
      try {
        // limit 2: after a Skip the current item is still needs_review, so the
        // server's FIRST waiting item can be this very one (newest-first sort)
        // — fetching one row falsely declared "last item" while dozens waited.
        // Pick the first OTHER id; only call it the last item when no other id
        // exists AND the server total agrees.
        const res = await catalogProductsApi.list({
          needs_review: true,
          is_active: 'all',
          limit: 2,
        });
        const ids = (res.products || [])
          .map((p) => String(p.id || ''))
          .filter(Boolean);
        if (ids.length === 0) {
          setQueueClear(true);
          return;
        }
        const nid = ids.find((id) => id !== currentId) || '';
        if (!nid) {
          // The server's only item IS this one.
          toast.info(
            Number(res.total ?? 0) > 1
              ? 'Could not find the next review item — go back to the queue.'
              : 'This is the last item waiting for review.'
          );
          return;
        }
        writeReviewQueue({ ids: [nid], index: 0, total: Number(res.total ?? 1) });
        openReviewItem(nid);
      } catch {
        toast.error('Could not find the next review item.');
      }
    },
    [editMode, openReviewItem, toast]
  );

  const confirmLeaveReviewItem = useCallback((): boolean => {
    if (!reviewDirty) return true;
    return window.confirm('You have unsaved changes on this item — move on without saving?');
  }, [reviewDirty]);

  // Skip / Next: advance without saving (dirty-confirm first).
  const handleReviewSkip = useCallback(() => {
    if (!confirmLeaveReviewItem()) return;
    void advanceReview('skip');
  }, [confirmLeaveReviewItem, advanceReview]);

  const handleReviewPrev = useCallback(() => {
    if (editMode?.kind !== 'catalog' || !queuePos?.hasPrev) return;
    if (!confirmLeaveReviewItem()) return;
    const stash = readReviewQueue();
    if (!stash) return;
    const at = stash.ids.indexOf(editMode.id);
    if (at <= 0) return;
    writeReviewQueue({ ...stash, index: at - 1 });
    openReviewItem(stash.ids[at - 1]);
  }, [editMode, queuePos, confirmLeaveReviewItem, openReviewItem]);

  // "Back to queue" — restore the exact review-queue position on its own
  // address (/catalog/review?page=N&focus=<id>; ?focus re-opens the card).
  const handleBackToQueue = useCallback(() => {
    if (editMode?.kind !== 'catalog') return;
    if (!confirmLeaveReviewItem()) return;
    const pg = readReviewQueue()?.filters?.page;
    navigate(
      `/catalog/review?${pg && pg > 1 ? `page=${pg}&` : ''}focus=${encodeURIComponent(editMode.id)}`
    );
  }, [editMode, confirmLeaveReviewItem, navigate]);

  // "Add photo" from the Catalog / Missing-photos list lands here as
  // ?edit=<id>#images (or ?review=<id>#images): once the product is loaded,
  // bring the image uploader into view. The uploader itself is the ONE
  // existing upload path (uploadImageFiles above); nothing is duplicated.
  // The intent is captured at FIRST render, not read in the effect: the ?edit
  // loader clears its param with setSearchParams, and react-router's setter
  // navigates to a search-only path -- which drops the hash before the effect
  // ever runs. Fires once.
  const wantImagesRef = useRef(window.location.hash === '#images');
  useEffect(() => {
    if (!editMode || !wantImagesRef.current) return;
    wantImagesRef.current = false;
    document.getElementById('product-images')?.scrollIntoView({ block: 'start' });
  }, [editMode]);

  // Save fixes: lenient validation -> DIFF-ONLY PUT (with the optimistic-
  // concurrency stamp) -> re-seed form + snapshot from the response (so the
  // server-derived HSN/GST/title become visible) -> re-run the dry-run.
  // Returns {ok, saved} so Save & Approve can chain it silently.
  const handleReviewSave = useCallback(
    async (opts?: { silent?: boolean }): Promise<{ ok: boolean; saved: boolean }> => {
      if (editMode?.kind !== 'catalog' || !reviewSnapshot) return { ok: false, saved: false };
      const values = currentValues();
      const newErrors = validateReviewForm(values, reviewSnapshot.values);
      setErrors(newErrors);
      if (Object.keys(newErrors).length > 0) {
        setOpenSections((s) => ({ ...s, identity: true, pricing: true }));
        toast.error('Please fix the highlighted fields.');
        return { ok: false, saved: false };
      }
      const payload = formValuesToCatalogUpdate(
        values,
        reviewSnapshot.values,
        String(reviewSnapshot.doc.updated_at || '') || undefined
      );
      if (Object.keys(payload).filter((k) => k !== 'expected_updated_at').length === 0) {
        if (!opts?.silent) toast.info('Nothing changed yet.');
        return { ok: true, saved: false };
      }
      // Currency guard (per-invocation closure id): a slow response for item
      // A must never stomp item B's form/snapshot after queue navigation.
      const id = editMode.id;
      setIsSubmitting(true);
      try {
        const res = await catalogProductsApi.update(id, payload);
        if (reviewIdRef.current !== id) return { ok: true, saved: true };
        const fresh = res.product;
        const v = catalogDocToFormValues(fresh);
        applyFormValues(v);
        setReviewSnapshot({ doc: fresh, values: v });
        if (!opts?.silent) toast.success('Saved — re-checking readiness…');
        void runReviewDryRun(id);
        return { ok: true, saved: true };
      } catch (e: unknown) {
        // Stale continuation: the reviewer moved on — drop the error quietly
        // (the item keeps needs_review; it resurfaces in the queue).
        if (reviewIdRef.current !== id) return { ok: false, saved: false };
        if (e instanceof CatalogRequestError && e.status === 409) {
          // Optimistic-concurrency conflict: reload the latest doc, RE-APPLY
          // the reviewer's changed fields on top, and let them save again.
          try {
            const freshDoc = await catalogProductsApi.get(id);
            if (reviewIdRef.current !== id) return { ok: false, saved: false };
            const freshValues = catalogDocToFormValues(freshDoc);
            applyFormValues(overlayChangedFormValues(freshValues, reviewSnapshot.values, values));
            setReviewSnapshot({ doc: freshDoc, values: freshValues });
            void runReviewDryRun(id);
            toast.warning(
              'This item was changed by someone else — reloaded the latest version and kept your edits. Please review and save again.',
              9000
            );
          } catch {
            toast.error(
              'This item was changed by someone else and could not be reloaded — go back to the queue and reopen it.'
            );
          }
        } else if (e instanceof CatalogRequestError && e.status === 422) {
          // Dictionary/validation 422: point at the offending field inline
          // (the message names the allowed values) when we can identify it.
          const field = dictionaryErrorField(e.message, values.category);
          if (field) {
            setErrors((prev) => ({ ...prev, [field]: e.message }));
            setOpenSections((s) => ({ ...s, identity: true }));
            toast.error('Please fix the highlighted fields.');
          } else {
            toast.error(e.message);
          }
        } else {
          toast.error(
            e instanceof Error && e.message ? e.message : 'Could not save the fixes.'
          );
        }
        return { ok: false, saved: false };
      } finally {
        setIsSubmitting(false);
      }
    },
    [
      editMode, reviewSnapshot, currentValues, applyFormValues, runReviewDryRun, toast,
    ]
  );

  // Save & Approve: sequential await PUT-if-dirty -> promote dry-run -> if ok
  // promote -> advance. Dry-run gaps render INLINE on failure. Promote 409
  // "already a billing product" = treat as approved-and-advance; other 409
  // (SKU clash / race) = toast + advance; anything else stays put.
  const handleReviewApprove = useCallback(async () => {
    if (editMode?.kind !== 'catalog' || reviewApproving || isSubmitting) return;
    // Same per-invocation currency discipline as handleReviewSave: a slow
    // response must never steer state (or the queue) for a different item.
    const id = editMode.id;
    setReviewApproving(true);
    try {
      const saveRes = await handleReviewSave({ silent: true });
      if (!saveRes.ok || reviewIdRef.current !== id) return;
      let dry: PromoteDryRunResult;
      try {
        dry = await catalogProductsApi.promoteDryRun(id);
      } catch (e: unknown) {
        if (reviewIdRef.current !== id) return;
        toast.error(
          e instanceof Error && e.message ? e.message : 'Could not check readiness — try again.'
        );
        return;
      }
      if (reviewIdRef.current !== id) return;
      setReviewDry(dry);
      if (!dry.ok) {
        const mapped = promoteGapsToFormErrors(dry.gaps, currentValues().category);
        setErrors((prev) => ({ ...prev, ...mapped.errors }));
        setOpenSections((s) => ({ ...s, identity: true, pricing: true }));
        toast.error('Not ready yet — fix the highlighted gaps first.');
        return;
      }
      try {
        const res = await catalogProductsApi.promote(id);
        toast.success(`Approved for POS${res.sku ? ` — SKU ${res.sku}` : ''}.`);
        if (reviewIdRef.current !== id) {
          // Approved, but the reviewer already moved on: just drop it from
          // the stash — do NOT navigate them again.
          removeFromReviewQueue(id);
          return;
        }
        await advanceReview('remove');
      } catch (pe: unknown) {
        if (pe instanceof CatalogRequestError && pe.status === 409) {
          if (reviewIdRef.current !== id) {
            removeFromReviewQueue(id);
            return;
          }
          if (/already a billing product/i.test(pe.message)) {
            toast.success('Already approved — moving on.');
          } else {
            toast.warning(pe.message, 9000);
          }
          await advanceReview('remove');
        } else {
          if (reviewIdRef.current !== id) return;
          toast.error(
            pe instanceof Error && pe.message ? pe.message : 'Approval failed — see the checklist.'
          );
          void runReviewDryRun(id);
        }
      }
    } finally {
      setReviewApproving(false);
    }
  }, [
    editMode, reviewApproving, isSubmitting, handleReviewSave, currentValues,
    advanceReview, runReviewDryRun, toast,
  ]);

  // Live gap chips for the ReviewQueueBar: dry-run gaps mapped onto the form's
  // vocabulary (field chips for rendered inputs, full messages for the rest).
  const reviewGapView = useMemo(() => {
    if (!reviewDry || reviewDry.ok) return { chips: [] as Array<{ key: string; label: string; title: string }>, other: [] as string[] };
    const { errors: mapped, other } = promoteGapsToFormErrors(reviewDry.gaps, selectedCategory);
    return {
      chips: Object.entries(mapped).map(([f, msg]) => ({
        key: f,
        label: fieldLabelFor(selectedCategory, f),
        title: msg,
      })),
      other,
    };
  }, [reviewDry, selectedCategory]);

  // What the validator would refuse RIGHT NOW (create / edit modes only --
  // review mode has its own lenient validator and the dry-run gap chips).
  // Feeds the "Still missing" row and the per-section "N to fix" counts, so
  // the list is the validator's own, never a second one. Inline red messages
  // stay submit-driven (`errors`) -- nobody wants 27 red lines while typing.
  // registryReady is a dependency so the set updates when the server's
  // required flags arrive.
  const liveErrors = useMemo(() => {
    void registryReady; // re-run once the server's required flags arrive
    return isReviewMode ? {} : validateProductForm(currentValues());
  }, [isReviewMode, currentValues, registryReady]);
  const sectionIssues = useMemo(() => {
    const n: Record<SectionId, number> = { identity: 0, pricing: 0, inventory: 0 };
    Object.keys(liveErrors).forEach((k) => { n[sectionOfError(k)] += 1; });
    return n;
  }, [liveErrors]);

  // Keyboard-first, remapped per mode:
  //   create/spine: Ctrl+Enter = Save, Ctrl+Shift+Enter = Save + New,
  //                 Esc exits variant mode (unchanged behaviour).
  //   review:       Ctrl+Enter = Save fixes, Ctrl+Shift+Enter = Save & Approve,
  //                 Alt+ArrowLeft/Right = Prev/Next (dirty-confirm).
  // While the duplicate-rescue popup is open it OWNS the keyboard — suppressed.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (dupInfo) return;
      if (editMode?.kind === 'catalog') {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          if (isSubmitting || reviewApproving) return;
          if (e.shiftKey) void handleReviewApprove();
          else void handleReviewSave();
        } else if (e.altKey && e.key === 'ArrowLeft') {
          e.preventDefault();
          // Same busy gate as the ReviewQueueBar buttons (disabled={busy}):
          // navigating mid-save would let a slow response land on the next item.
          if (isSubmitting || reviewApproving) return;
          handleReviewPrev();
        } else if (e.altKey && e.key === 'ArrowRight') {
          e.preventDefault();
          if (isSubmitting || reviewApproving) return;
          handleReviewSkip();
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        if (isSubmitting) return;
        void handleSubmit(e.shiftKey);
      } else if (e.key === 'Escape' && variantCtx) {
        e.preventDefault();
        exitVariantMode();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    handleSubmit, isSubmitting, dupInfo, variantCtx, exitVariantMode, editMode,
    reviewApproving, handleReviewSave, handleReviewApprove, handleReviewPrev,
    handleReviewSkip,
  ]);

  // ---- Templates: list / load / save / delete ------------------------------
  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    try {
      const res = await productTemplatesApi.list();
      setTemplates(res.templates || []);
      setTemplatesLoaded(true);
    } catch {
      toast.error('Could not load templates.');
    } finally {
      setTemplatesLoading(false);
    }
  }, [toast]);

  // Lazy-load the list the first time the panel is opened.
  useEffect(() => {
    if (templatesOpen && !templatesLoaded && !templatesLoading) {
      void loadTemplates();
    }
  }, [templatesOpen, templatesLoaded, templatesLoading, loadTemplates]);

  const handleLoadTemplate = useCallback(
    (tpl: ProductTemplate) => {
      // A template load replaces the whole form — leave variant mode if active.
      setVariantCtx(null);
      setFlaggedFields(new Set());
      applyFormValues(tpl.payload);
      setTemplatesOpen(false);
      toast.success(`Loaded template "${tpl.name}". Edit and save as a new product.`);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [applyFormValues, toast]
  );

  const handleSaveTemplate = useCallback(async () => {
    const name = saveName.trim();
    if (!name) {
      toast.error('Give the template a name first.');
      return;
    }
    if (!selectedCategory) {
      toast.error('Pick a category before saving a template.');
      return;
    }
    setSavingTemplate(true);
    try {
      const created = await productTemplatesApi.create(name, currentValues(), selectedCategory);
      // Prepend so it shows at the top of the (newest-first) list.
      setTemplates((prev) => [created, ...prev.filter((t) => t.template_id !== created.template_id)]);
      setSaveName('');
      toast.success(`Saved template "${created.name}".`);
    } catch {
      toast.error('Failed to save template.');
    } finally {
      setSavingTemplate(false);
    }
  }, [saveName, selectedCategory, currentValues, toast]);

  const handleDeleteTemplate = useCallback(
    async (tpl: ProductTemplate) => {
      try {
        await productTemplatesApi.remove(tpl.template_id);
        setTemplates((prev) => prev.filter((t) => t.template_id !== tpl.template_id));
        toast.success(`Deleted template "${tpl.name}".`);
      } catch {
        toast.error('Could not delete this template (you may not own it).');
      }
    },
    [toast]
  );

  // ---- Clone: prefill from an existing product -----------------------------
  const cloneFromProduct = useCallback(
    (product: ProductDoc) => {
      // A clone replaces the whole form — leave variant mode if active.
      setVariantCtx(null);
      setFlaggedFields(new Set());
      applyFormValues(productToFormValues(product));
      toast.success('Cloned into the form. Tweak the details and save as a NEW SKU.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [applyFormValues, toast]
  );

  const handleCloneFromSku = useCallback(async () => {
    const sku = cloneSku.trim();
    if (!sku) {
      toast.error('Enter a SKU or barcode to clone.');
      return;
    }
    setCloning(true);
    try {
      // searchProducts hits GET /products?search= — match the exact SKU/barcode.
      const res = await productApi.searchProducts(sku);
      const list: ProductDoc[] = (res?.products || res || []) as ProductDoc[];
      const match =
        list.find(
          (p) =>
            String(p.sku || '').toLowerCase() === sku.toLowerCase() ||
            String(p.barcode || '').toLowerCase() === sku.toLowerCase()
        ) || list[0];
      if (!match) {
        toast.error(`No product found for "${sku}".`);
        return;
      }
      cloneFromProduct(match);
      setCloneSku('');
      setTemplatesOpen(false);
    } catch {
      toast.error('Could not look up that product.');
    } finally {
      setCloning(false);
    }
  }, [cloneSku, cloneFromProduct, toast]);

  // Deep-link clone: /catalog/add?clone=<productId> prefills from that product
  // (e.g. a "Clone" button on the inventory list can link straight here). Runs
  // once per id; clears the param so a manual reset isn't re-clobbered.
  const cloneId = searchParams.get('clone');
  useEffect(() => {
    // ?review wins over a crafted combined URL — clone is a create affordance
    // and is suppressed in review mode.
    if (!cloneId || searchParams.get('review')) return;
    let cancelled = false;
    (async () => {
      try {
        const product = (await productApi.getProduct(cloneId)) as ProductDoc;
        if (!cancelled && product) {
          cloneFromProduct(product);
        }
      } catch {
        if (!cancelled) toast.error('Could not load the product to clone.');
      } finally {
        if (!cancelled) {
          const next = new URLSearchParams(searchParams);
          next.delete('clone');
          setSearchParams(next, { replace: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloneId]);

  // Deep-link edit: /catalog/add?edit=<productId> loads that product into the
  // form for EDIT-IN-PLACE (verbatim shape of the ?clone= loader above —
  // getProduct -> productToFormValues -> applyFormValues) and flips the page
  // into edit mode. Runs once per id; clears the param so a manual reset
  // isn't re-clobbered.
  const editId = searchParams.get('edit');
  useEffect(() => {
    // ?review wins over a crafted combined URL.
    if (!editId || searchParams.get('review')) return;
    let cancelled = false;
    (async () => {
      try {
        const product = (await productApi.getProduct(editId)) as ProductDoc;
        if (!cancelled && product) {
          // Edit replaces the whole form — leave variant mode if active.
          setVariantCtx(null);
          setFlaggedFields(new Set());
          applyFormValues(productToFormValues(product));
          setEditMode({ kind: 'spine', id: editId, sku: String(product.sku || '') });
          // Prefill the reorder level so the single PUT round-trips it.
          const rp = Number((product as { reorder_point?: unknown }).reorder_point);
          setReorderLevel(Number.isFinite(rp) && rp >= 0 ? String(rp) : '5');
        }
      } catch {
        if (!cancelled) toast.error('Could not load the product to edit.');
      } finally {
        if (!cancelled) {
          const next = new URLSearchParams(searchParams);
          next.delete('edit');
          setSearchParams(next, { replace: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editId]);

  // ---- FULL-PAGE REVIEW loader: /catalog/add?review=<catalogDocId> ----------
  // Mirrors the ?edit loader above but against the catalog_products PIM doc
  // (catalogProductsApi.get -> catalogDocToFormValues -> applyFormValues).
  // Unlike ?edit, the param STAYS in the URL — advancing through the queue is
  // a same-page param swap that re-runs this effect for the next id. An
  // already-approved (pos_ready) doc URL-replaces to ?edit=<id>; a vanished id
  // falls forward to the item now at the same queue index (else the server
  // fallback / the "queue clear" panel).
  const reviewId = searchParams.get('review');
  useEffect(() => {
    // Synchronous currency stamp: in-flight save/approve continuations for a
    // PREVIOUS id compare against this and drop themselves.
    reviewIdRef.current = reviewId;
    if (!reviewId) return;
    let cancelled = false;
    (async () => {
      try {
        const doc = await catalogProductsApi.get(reviewId);
        if (cancelled) return;
        if (doc.pos_ready) {
          removeFromReviewQueue(reviewId);
          toast.info('This item is already approved — opening the standard editor.');
          const next = new URLSearchParams(searchParams);
          next.delete('review');
          next.set('edit', reviewId);
          setSearchParams(next, { replace: true });
          return;
        }
        // Review replaces the whole form — leave variant mode if active.
        setVariantCtx(null);
        setFlaggedFields(new Set());
        setDupInfo(null);
        const v = catalogDocToFormValues(doc);
        applyFormValues(v);
        setEditMode({ kind: 'catalog', id: reviewId, sku: String(doc.sku || '') });
        setReviewSnapshot({ doc, values: v });
        setReviewDry(null);
        setQueueClear(false);
        // Queue position from the stash (a deep link gets a one-item stash so
        // Next still works via the server fallback).
        const stash = readReviewQueue();
        if (stash && stash.ids.includes(reviewId)) {
          const at = stash.ids.indexOf(reviewId);
          if (stash.index !== at) writeReviewQueue({ ...stash, index: at });
          setQueuePos({
            n: (stash.offset ?? 0) + at + 1,
            m: stash.total ?? stash.ids.length,
            hasPrev: at > 0,
          });
        } else {
          writeReviewQueue({ ids: [reviewId], index: 0 });
          setQueuePos(null);
        }
        void runReviewDryRun(reviewId);
        window.scrollTo({ top: 0 });
      } catch (err: unknown) {
        if (cancelled) return;
        // Only a REAL 404 means the item vanished (approved elsewhere /
        // removed). Any other failure — network blip, 5xx, timeout — is
        // transient: the item is still reviewable, so leave the stash intact
        // and let the reviewer retry instead of silently ejecting it.
        if (!(err instanceof CatalogRequestError && err.status === 404)) {
          toast.error(
            'Could not load this review item — check your connection and try again.'
          );
          return;
        }
        // Vanished: fall FORWARD to the item now at the same stash index,
        // else ask the server for the next one.
        const stash = readReviewQueue();
        const at = stash ? stash.ids.indexOf(reviewId) : -1;
        const after = removeFromReviewQueue(reviewId);
        const nextId = after && at >= 0 ? after.ids[at] : undefined;
        if (nextId) {
          toast.info('That item is no longer in the queue — moved to the next one.');
          if (after) writeReviewQueue({ ...after, index: at });
          const next = new URLSearchParams(searchParams);
          next.set('review', nextId);
          setSearchParams(next, { replace: true });
          return;
        }
        try {
          // limit 2: the server's first waiting item can BE this id (stale
          // list); pick the first OTHER id instead of wrongly declaring the
          // queue clear.
          const res = await catalogProductsApi.list({
            needs_review: true,
            is_active: 'all',
            limit: 2,
          });
          const ids = (res.products || [])
            .map((p) => String(p.id || ''))
            .filter(Boolean);
          const nid = ids.find((id) => id !== reviewId) || '';
          if (nid) {
            writeReviewQueue({ ids: [nid], index: 0, total: Number(res.total ?? 1) });
            const next = new URLSearchParams(searchParams);
            next.set('review', nid);
            setSearchParams(next, { replace: true });
          } else {
            setQueueClear(true);
          }
        } catch {
          toast.error('Could not load that review item.');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewId]);

  // Leaving review mode via the URL (e.g. the sidebar's "Add product" while
  // reviewing removes ?review=): drop back to a fresh create form so review
  // state never leaks into a create.
  useEffect(() => {
    if (reviewId || editMode?.kind !== 'catalog') return;
    setEditMode(null);
    setReviewSnapshot(null);
    setReviewDry(null);
    setQueuePos(null);
    setQueueClear(false);
    resetForm(false);
  }, [reviewId, editMode, resetForm]);

  // Deep-link variant: /catalog/add?variant=<productId> enters VARIANT MODE
  // seeded from that product — the same code path the duplicate-rescue popup's
  // "Add a new colour/size" uses. The "+ Variant" button in the product list
  // emits exactly this URL. Runs once per id; clears the param so a manual
  // reset isn't re-clobbered.
  const variantId = searchParams.get('variant');
  useEffect(() => {
    // ?review wins over a crafted combined URL — variant is a create
    // affordance and is suppressed in review mode.
    if (!variantId || searchParams.get('review')) return;
    let cancelled = false;
    (async () => {
      try {
        const product = (await productApi.getProduct(variantId)) as ProductDoc;
        if (!cancelled && product) {
          enterVariantMode(product);
        }
      } catch {
        if (!cancelled) toast.error('Could not load the product to add a variant of.');
      } finally {
        if (!cancelled) {
          const next = new URLSearchParams(searchParams);
          next.delete('variant');
          setSearchParams(next, { replace: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variantId]);


  const setAttr = (name: string, value: string) => {
    setAttributes((prev) => ({ ...prev, [name]: value }));
    // Variant mode: touching a copied field confirms it — drop the amber flag.
    clearFlag(name);
  };

  const toggleSection = (id: SectionId) =>
    setOpenSections((s) => ({ ...s, [id]: !s[id] }));

  // One object, handed to every block. Flat on purpose: the blocks destructure
  // exactly the names the JSX used when it all lived in one component, so the
  // moved markup is byte-for-byte what it was.
  return {
    // identity / who
    user, canAddProduct, canSeeCost, registryReady,
    // routing
    navigate, setSearchParams,
    // form values
    selectedCategory, setSelectedCategory,
    attributes, setAttributes, setAttr,
    description, setDescription,
    hsnCode, setHsnCode, gstRate, setGstRate, hsnMatchesCategory,
    weight, setWeight,
    mrp, setMrp, offerPrice, setOfferPrice, costPrice, setCostPrice,
    discountCategory,
    reorderLevel, setReorderLevel,
    syncToShopify, setSyncToShopify, shopifyTags, setShopifyTags,
    publishPOS, setPublishPOS,
    images, setImages,
    displayName, setDisplayName, reviewTags, setReviewTags,
    // options fed from the server
    subbrandsByBrand, brandTiers,
    // accordion + validation surface
    errors, showAdvanced, setShowAdvanced,
    openSections, toggleSection, liveErrors, sectionIssues, jumpToField,
    firstFieldRef,
    // modes
    editMode, isReviewMode, variantCtx, exitVariantMode,
    flaggedFields, clearFlag,
    // templates + clone
    templatesOpen, setTemplatesOpen, templates, templatesLoading,
    saveName, setSaveName, savingTemplate, handleSaveTemplate,
    cloneSku, setCloneSku, cloning, handleCloneFromSku,
    handleLoadTemplate, handleDeleteTemplate,
    // duplicate rescue
    dupInfo, setDupInfo, dupBusy, handleDupAddVariant, handleDupOpenExisting,
    // similar products
    handleSimilarPick, handleSimilarOpen,
    // submit
    isSubmitting, handleSubmit,
    // review mode
    queueClear, setQueueClear, queuePos, reviewSnapshot, reviewDirty,
    reviewApproving, reviewDry, reviewDryLoading, reviewGapView,
    handleReviewSave, handleReviewApprove, handleReviewSkip, handleReviewPrev,
    handleBackToQueue,
    // product images
    ...imageCtl,
  };
}

/** The whole form, as handed to each block. */
export type QuickAddForm = ReturnType<typeof useQuickAddForm>;
