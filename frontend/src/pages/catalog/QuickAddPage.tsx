// ============================================================================
// IMS 2.0 - Quick Add (the single product-add door)
// ============================================================================
// The SOLE product-add screen at /catalog/add. The older "Guided" (6-step
// wizard) and "Bulk" (Rapid Grid) modes + the Single|Guided|Bulk toggle were
// removed; this one-screen form absorbed EVERY field, section, button and
// validation Guided had, so nothing was lost:
//   - Accordion sections: Identity / Pricing / Inventory / Online
//   - Smart defaults (category -> HSN/GST auto) collapsed under "Advanced"
//     (incl. HSN-required marker + GST-compliance note carried over from Guided)
//   - Cost price + margin are role-gated (F35), as in Guided
//   - Product Images placeholder section (parity with Guided)
//   - Live Review summary rail (lists every filled attribute, like Guided's review)
//   - Ctrl+Enter = Save ; Ctrl+Shift+Enter = Save + New (keeps category + brand)
// Shares CATEGORY_FIELDS + the create payload mapping via productAddShared.ts so
// the create contract + per-category required-field enforcement are unchanged.

import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import {
  Save,
  RotateCcw,
  Loader2,
  ChevronDown,
  X,
  Tag,
  IndianRupee,
  Boxes,
  Globe,
  Sparkles,
  Keyboard,
  LayoutTemplate,
  Copy,
  Trash2,
  Search,
  Upload,
  Image as ImageIcon,
  Wand2,
  Gauge,
  ShieldCheck,
  AlertTriangle,
  PackagePlus,
  Info,
  Sparkles as SparklesIcon,
  ExternalLink,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { productApi } from '../../services/api/products';
import {
  catalogAutopilotApi,
  AI_ENRICH_SOURCE,
  type AutopilotCandidate,
} from '../../services/api/catalogAutopilot';
// Import the templates service DIRECTLY from its module (not the api barrel —
// the barrel re-export fails to resolve for new services, TS2614).
import { productTemplatesApi, type ProductTemplate } from '../../services/api/productTemplates';
import { getHSNOptions } from '../../constants/gst';
import {
  CATEGORIES,
  getCategoryFields,
  loadCategoryRegistry,
  categoryName,
  validateProductForm,
  buildProductPayload,
  resolveHsnGst,
  productToFormValues,
  mapAutopilotCandidate,
  candidateReferences,
  candidateImagesUsable,
  imageRehostSummary,
  takeAutopilotPrefill,
  AUTOPILOT_PREFILL_PARAM,
  AUTOPILOT_PREFILL_VALUE,
  type CategoryField,
  type ProductFormValues,
  type ProductDoc,
} from './productAddShared';
import clsx from 'clsx';

type SectionId = 'identity' | 'pricing' | 'inventory' | 'online';

export function QuickAddPage() {
  const { hasRole } = useAuth();
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
  const [uploadingImages, setUploadingImages] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const imageInputRef = useRef<HTMLInputElement | null>(null);

  // Inline Catalog Autopilot panel: collapsed by default so the manual flow
  // is unchanged. v2 search inputs = brand + model + colour + size + category;
  // "Use this" fills EVERY mapped field + images and the operator verifies.
  const [autopilotOpen, setAutopilotOpen] = useState(false);
  const [apBrand, setApBrand] = useState('');
  const [apModel, setApModel] = useState('');
  const [apColor, setApColor] = useState('');
  const [apSize, setApSize] = useState('');
  // Category for the search when the operator starts with Autopilot first;
  // the form's own picked category always wins when set.
  const [apCategory, setApCategory] = useState('');
  const [apLoading, setApLoading] = useState(false);
  const [apSearched, setApSearched] = useState(false);
  const [apCandidates, setApCandidates] = useState<AutopilotCandidate[]>([]);
  // v2 "operator verifies" UX: which attribute fields Autopilot filled (auto
  // chips + highlight), the unmapped extra specs found, and the reference
  // URL(s) the data came from. Cleared on reset / manual edit of a field.
  const [autoFilled, setAutoFilled] = useState<Set<string>>(new Set());
  const [apExtras, setApExtras] = useState<Record<string, string>>({});
  const [apRefUrls, setApRefUrls] = useState<string[]>([]);
  // Image RE-HOST outcome: how many candidate images were COPIED into our own
  // storage vs KEPT as external hotlinks (re-host failed / rights disallow).
  const [apImageCopy, setApImageCopy] = useState<{ copied: number; kept: number } | null>(null);
  // The last used candidate — re-mapped when the operator picks a category
  // AFTER staging (so staged specs still land on the category's real fields).
  const stagedCandidateRef = useRef<AutopilotCandidate | null>(null);

  // UI state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [useAdvancedHSN, setUseAdvancedHSN] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [openSections, setOpenSections] = useState<Record<SectionId, boolean>>({
    identity: true,
    pricing: true,
    inventory: true,
    online: false, // collapsed by default per the design
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

  const firstFieldRef = useRef<HTMLSelectElement | HTMLInputElement | null>(null);
  // Bump on registry load so the field list (required markers sourced from the
  // canonical server registry) re-renders once it arrives.
  const [registryReady, setRegistryReady] = useState(false);

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
  // behaviour as the wizard's useEffect.
  useEffect(() => {
    if (skipHsnAutofillRef.current) {
      skipHsnAutofillRef.current = false;
      return;
    }
    if (selectedCategory) {
      const { hsnCode: hc, gstRate: gr } = resolveHsnGst(selectedCategory, useAdvancedHSN);
      if (hc) setHsnCode(hc);
      setGstRate(gr);
    }
  }, [selectedCategory, useAdvancedHSN]);

  // Keyboard-first: when a category is picked, move focus to the first
  // category field so the user can start typing without reaching for the mouse.
  useEffect(() => {
    if (!selectedCategory) return;
    const t = window.setTimeout(() => firstFieldRef.current?.focus(), 60);
    return () => window.clearTimeout(t);
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
    }),
    [
      selectedCategory, attributes, description, hsnCode, gstRate, weight, mrp,
      offerPrice, costPrice, discountCategory, syncToShopify, shopifyTags, publishPOS,
      images,
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
      // Clear the v2 Autopilot verify state so a fresh product starts clean.
      setAutoFilled(new Set());
      setApExtras({});
      setApRefUrls([]);
      setApImageCopy(null);
      stagedCandidateRef.current = null;
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
    setErrors({});
    // Reveal Inventory too when a prefill carries images so they're visible.
    setOpenSections((s) => ({
      ...s,
      identity: true,
      pricing: true,
      ...(Array.isArray(v.images) && v.images.length > 0 ? { inventory: true } : {}),
    }));
  }, []);

  // ---- Product image upload (Part 1) ---------------------------------------
  // Upload each selected/dropped file via productApi.uploadProductImage and
  // append the returned self-hosted URL to `images`. Fail-soft: a failed upload
  // just isn't added (a toast names how many, so nothing silently vanishes).
  const uploadImageFiles = useCallback(
    async (files: File[]) => {
      const imageFiles = files.filter((f) => f.type.startsWith('image/'));
      if (imageFiles.length === 0) {
        if (files.length > 0) toast.error('Only image files can be uploaded.');
        return;
      }
      setUploadingImages(true);
      let failed = 0;
      const uploaded: string[] = [];
      for (const file of imageFiles) {
        try {
          const res = await productApi.uploadProductImage(file);
          if (res?.url) uploaded.push(res.url);
          else failed += 1;
        } catch {
          failed += 1;
        }
      }
      if (uploaded.length > 0) {
        setImages((prev) => [...prev, ...uploaded]);
      }
      if (failed > 0) {
        toast.warning(
          `${failed} image${failed > 1 ? 's' : ''} could not be uploaded${uploaded.length ? ' (the rest were added)' : ''}.`
        );
      } else if (uploaded.length > 0) {
        toast.success(`${uploaded.length} image${uploaded.length > 1 ? 's' : ''} uploaded.`);
      }
      setUploadingImages(false);
    },
    [toast]
  );

  const onImageInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files ? Array.from(e.target.files) : [];
      void uploadImageFiles(files);
      // Reset so the same file can be re-picked after a remove.
      e.target.value = '';
    },
    [uploadImageFiles]
  );

  const onImageDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragActive(false);
      const files = e.dataTransfer?.files ? Array.from(e.dataTransfer.files) : [];
      void uploadImageFiles(files);
    },
    [uploadImageFiles]
  );

  const removeImage = useCallback((url: string) => {
    setImages((prev) => prev.filter((u) => u !== url));
  }, []);

  // ---- Inline Catalog Autopilot (v2) ----------------------------------------
  // The search knows its category: the form's already-picked category wins,
  // else the panel's own category select (for autopilot-first flows).
  const apEffectiveCategory = selectedCategory || apCategory;

  const runAutopilotSearch = useCallback(async () => {
    if (!apBrand.trim() || !apModel.trim()) {
      toast.error('Brand and model are required to search.');
      return;
    }
    setApLoading(true);
    setApSearched(false);
    try {
      const r = await catalogAutopilotApi.createJob({
        brand: apBrand.trim(),
        model: apModel.trim(),
        color: apColor.trim(),
        size: apSize.trim(),
        category: apEffectiveCategory,
      });
      setApCandidates(r.candidates || []);
      setApSearched(true);
      if (!r.candidate_count) toast.info('No candidates from active sources yet.');
    } catch {
      toast.error('Autopilot search failed.');
    } finally {
      setApLoading(false);
    }
  }, [apBrand, apModel, apColor, apSize, apEffectiveCategory, toast]);

  // Shared "apply a candidate" for BOTH the inline "Use this" and the
  // ?prefill=autopilot handoff: maps every field, marks what was auto-filled,
  // surfaces the unmapped extras + references, summarises the fill, and then
  // RE-HOSTS the candidate's images into our own storage (fail-soft).
  const applyAutopilotCandidate = useCallback(
    async (c: AutopilotCandidate) => {
      // Category resolution (the owner's #1 gripe was a fill that "did
      // nothing" because no category was set, so no fields rendered):
      //   1. the form's own picked category (never overridden),
      //   2. the panel's category selector (autopilot-first flows),
      //   3. the job stamp on the candidate / title inference (inside
      //      mapAutopilotCandidate),
      //   4. else we stage + prompt, and the late-category effect fills the
      //      fields the moment a category is picked.
      const res = mapAutopilotCandidate(c, selectedCategory || apCategory || undefined);
      applyFormValues(res.values);
      setAutoFilled(new Set(res.autoFilled));
      setApExtras(res.extras);
      setApRefUrls(res.referenceUrls);
      setApImageCopy(null);
      stagedCandidateRef.current = c;

      const finalCategory = res.values.category;
      const imgCount = res.values.images?.length ?? 0;
      const refDomain = res.referenceUrls[0] ? apDomain(res.referenceUrls[0]) : '';
      if (!finalCategory) {
        toast.info(
          'Details staged — pick a category (panel selector or the form) and the fields will fill in automatically.'
        );
      } else {
        toast.success(
          `Filled ${res.autoFilled.length} fields` +
            (imgCount > 0 ? ` + ${imgCount} image${imgCount === 1 ? '' : 's'}` : '') +
            (refDomain ? ` from ${refDomain}` : '') +
            ' into the product form — please verify.'
        );
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });

      // RE-HOST (owner requirement): copy each candidate image into OUR file
      // store via POST /products/image/from-url so the product never hotlinks
      // a brand site. Concurrent + fail-soft: a failed copy KEEPS the external
      // link (nothing is lost); the fill summary reports copied vs kept.
      // Respect the image-rights rules — UNVERIFIED-source images without a
      // rights confirmation are never copied into our storage.
      const externals = res.values.images ?? [];
      if (externals.length === 0) return;
      if (!candidateImagesUsable(c)) {
        setApImageCopy({ copied: 0, kept: externals.length });
        return;
      }
      const settled = await Promise.allSettled(
        externals.map((u) => productApi.rehostProductImage(u))
      );
      const swap = new Map<string, string>();
      let copied = 0;
      settled.forEach((s, i) => {
        if (s.status === 'fulfilled' && s.value?.url) {
          swap.set(externals[i], s.value.url);
          copied += 1;
        }
      });
      if (copied > 0) {
        // Replace only the URLs we copied; the operator may have edited the
        // list meanwhile, so map over the CURRENT state.
        setImages((prev) => prev.map((u) => swap.get(u) || u));
      }
      setApImageCopy({ copied, kept: externals.length - copied });
    },
    [applyFormValues, selectedCategory, apCategory, toast]
  );

  const useAutopilotCandidate = applyAutopilotCandidate;

  // Late category pick: staged attributes survive AND get re-mapped onto the
  // newly picked category's real fields (only filling attributes the operator
  // hasn't already typed — a user value is never clobbered).
  useEffect(() => {
    const staged = stagedCandidateRef.current;
    if (!staged || !selectedCategory) return;
    const res = mapAutopilotCandidate(staged, selectedCategory);
    const additions: Record<string, string> = {};
    Object.entries(res.values.attributes).forEach(([k, v]) => {
      if (v && !attributes[k]) additions[k] = v;
    });
    if (Object.keys(additions).length === 0) return;
    setAttributes((prev) => ({ ...additions, ...prev }));
    setAutoFilled((s) => new Set([...s, ...Object.keys(additions)]));
    setApExtras(res.extras);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCategory, attributes]);

  const handleSubmit = useCallback(
    async (saveAndNew: boolean) => {
      const values = currentValues();
      const newErrors = validateProductForm(values);
      setErrors(newErrors);
      if (Object.keys(newErrors).length > 0) {
        // Make sure the section holding the first error is open. The discount
        // tier lives in the Pricing section, so a discount_category error must
        // open Pricing (not Identity) or the inline error would stay hidden.
        const pricingKeys = new Set(['mrp', 'offer_price', 'discount_category']);
        if (Object.keys(newErrors).some((k) => pricingKeys.has(k))) {
          setOpenSections((s) => ({ ...s, pricing: true }));
        }
        if (newErrors.category || Object.keys(newErrors).some((k) => !pricingKeys.has(k))) {
          setOpenSections((s) => ({ ...s, identity: true }));
        }
        toast.error('Please fix the highlighted fields.');
        return;
      }

      setIsSubmitting(true);
      try {
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
            : 'Product created successfully!'
        );
        if (saveAndNew) {
          resetForm(true);
          // Keep focus flowing — jump back to the top of the form.
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          navigate('/inventory');
        }
      } catch {
        toast.error('Failed to create product. Please try again.');
      } finally {
        setIsSubmitting(false);
      }
    },
    [currentValues, toast, resetForm, navigate]
  );

  // Keyboard-first: Ctrl+Enter = Save, Ctrl+Shift+Enter = Save + New.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        if (isSubmitting) return;
        void handleSubmit(e.shiftKey);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleSubmit, isSubmitting]);

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
    if (!cloneId) return;
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

  // Catalog Autopilot payoff: /catalog/add?prefill=autopilot reads the candidate
  // stashed in sessionStorage (set by "Create product from this" on the Autopilot
  // page) and prefills the form. One-shot: the candidate is consumed and the
  // param cleared so a manual edit/reset isn't re-clobbered on re-render.
  const prefillKind = searchParams.get(AUTOPILOT_PREFILL_PARAM);
  useEffect(() => {
    if (prefillKind !== AUTOPILOT_PREFILL_VALUE) return;
    const candidate = takeAutopilotPrefill();
    if (candidate) {
      // Same v2 richness as the inline "Use this": full field mapping, auto
      // chips, extras + reference summary.
      void applyAutopilotCandidate(candidate);
    } else {
      toast.info('Nothing to prefill — open a candidate from Catalog Autopilot first.');
    }
    const next = new URLSearchParams(searchParams);
    next.delete(AUTOPILOT_PREFILL_PARAM);
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillKind]);

  if (!canAddProduct) {
    return (
      <div className="inv-body">
        <div className="card text-center py-12">
          <h2 className="text-xl font-semibold text-gray-700">Access Denied</h2>
          <p className="text-gray-500 mt-1">You don't have permission to add products.</p>
        </div>
      </div>
    );
  }

  // Field list with required flags from the canonical registry (registryReady
  // is a render dependency so the markers update when the registry arrives).
  void registryReady;
  const fields: CategoryField[] = selectedCategory ? getCategoryFields(selectedCategory) : [];
  const isLens = selectedCategory === 'LS';
  // The lens stock-power fields are entered via the Power Grid, not here.
  const lensPowerFields = new Set(['sph', 'cyl', 'axis', 'add']);
  const visibleFields = isLens ? fields.filter((f) => !lensPowerFields.has(f.name)) : fields;

  const setAttr = (name: string, value: string) => {
    setAttributes((prev) => ({ ...prev, [name]: value }));
    // The operator touched this field — it is now verified, drop the auto chip.
    setAutoFilled((prev) => {
      if (!prev.has(name)) return prev;
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  };

  const toggleSection = (id: SectionId) =>
    setOpenSections((s) => ({ ...s, [id]: !s[id] }));

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

  // -------- small presentational helpers -----------------------------------
  const renderField = (field: CategoryField, autoFocus = false) => {
    // Autopilot filled this and the operator hasn't touched it yet: a subtle
    // highlight + "auto" chip flags it for verification (chip drops on edit).
    const isAuto = autoFilled.has(field.name);
    const fieldClass = clsx('input-field w-full', isAuto && 'ring-1 ring-violet-300 bg-violet-50/40');
    return (
    <div key={field.name}>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {field.label}
        {field.required && <span className="text-red-500 ml-1">*</span>}
        {isAuto && (
          <span
            className="ml-2 inline-flex items-center gap-0.5 px-1.5 py-px rounded bg-violet-100 text-violet-700 text-[10px] font-medium align-middle"
            title="Auto-filled by Autopilot — please verify"
          >
            <SparklesIcon className="w-2.5 h-2.5" /> auto
          </span>
        )}
      </label>
      {field.type === 'select' ? (
        <select
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          className={fieldClass}
        >
          <option value="">Select {field.label}</option>
          {field.options?.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : field.type === 'date' ? (
        <input
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          type="date"
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          className={fieldClass}
        />
      ) : (
        <input
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          type={field.type}
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          placeholder={field.placeholder || field.label}
          className={fieldClass}
        />
      )}
      {errors[field.name] && (
        <p className="text-red-500 text-xs mt-1">{errors[field.name]}</p>
      )}
    </div>
    );
  };

  const Section = ({
    id, title, icon, subtitle, children,
  }: {
    id: SectionId;
    title: string;
    icon: React.ReactNode;
    subtitle?: string;
    children: React.ReactNode;
  }) => {
    const open = openSections[id];
    return (
      <div className="card !p-0 overflow-hidden">
        <button
          type="button"
          onClick={() => toggleSection(id)}
          className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-gray-50 transition-colors"
          aria-expanded={open ? "true" : "false"}
        >
          <span className="flex items-center gap-3">
            <span className="text-bv">{icon}</span>
            <span>
              <span className="block font-semibold text-gray-900">{title}</span>
              {subtitle && <span className="block text-xs text-gray-500">{subtitle}</span>}
            </span>
          </span>
          <ChevronDown
            className={clsx('w-5 h-5 text-gray-400 transition-transform', open && 'rotate-180')}
          />
        </button>
        {open && <div className="px-5 pb-5 pt-1 border-t border-gray-100">{children}</div>}
      </div>
    );
  };

  return (
    <div className="inv-body">
      {/* Editorial header (mode toggle is rendered by the route shell) */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Catalog · Add product</div>
          <h1>One screen. One SKU. Fast.</h1>
          <div className="hint">
            Fill the essentials and hit <kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd> to save.
            Category sets HSN + GST automatically.
          </div>
        </div>

        {/* Templates + clone affordance */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setTemplatesOpen((v) => !v)}
            className="btn-secondary flex items-center gap-2"
            aria-expanded={templatesOpen ? "true" : "false"}
            aria-haspopup="dialog"
          >
            <LayoutTemplate className="w-4 h-4" />
            Templates
            <ChevronDown className={clsx('w-4 h-4 transition-transform', templatesOpen && 'rotate-180')} />
          </button>

          {templatesOpen && (
            <>
              {/* Click-away backdrop */}
              <button
                type="button"
                aria-label="Close templates"
                className="fixed inset-0 z-40 cursor-default"
                onClick={() => setTemplatesOpen(false)}
              />
              <div
                role="dialog"
                aria-label="Templates and clone"
                className="absolute right-0 z-50 mt-2 w-[340px] max-w-[92vw] rounded-xl border border-gray-200 bg-white shadow-xl"
              >
                {/* Save current as template */}
                <div className="p-4 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-900 mb-2">Save as template</p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={saveName}
                      onChange={(e) => setSaveName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          void handleSaveTemplate();
                        }
                      }}
                      placeholder="Template name"
                      className="input-field w-full"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveTemplate()}
                      disabled={savingTemplate}
                      className="btn-primary shrink-0 flex items-center gap-1.5"
                    >
                      {savingTemplate ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                      Save
                    </button>
                  </div>
                  <p className="text-xs text-gray-400 mt-1.5">Saves the current field values for reuse.</p>
                </div>

                {/* Clone from an existing SKU */}
                <div className="p-4 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-900 mb-2">Clone a product</p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={cloneSku}
                      onChange={(e) => setCloneSku(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          void handleCloneFromSku();
                        }
                      }}
                      placeholder="Enter SKU or barcode"
                      className="input-field w-full"
                    />
                    <button
                      type="button"
                      onClick={() => handleCloneFromSku()}
                      disabled={cloning}
                      className="btn-secondary shrink-0 flex items-center gap-1.5"
                    >
                      {cloning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Copy className="w-4 h-4" />}
                      Clone
                    </button>
                  </div>
                  <p className="text-xs text-gray-400 mt-1.5">Prefills the form; saves as a brand-new SKU.</p>
                </div>

                {/* Saved templates list */}
                <div className="p-4">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm font-semibold text-gray-900">Saved templates</p>
                    {templates.length > 0 && (
                      <span className="text-xs text-gray-400">{templates.length}</span>
                    )}
                  </div>

                  {templatesLoading ? (
                    <div className="flex items-center gap-2 text-sm text-gray-500 py-3">
                      <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                    </div>
                  ) : templates.length === 0 ? (
                    <div className="flex items-start gap-2 text-sm text-gray-500 py-2">
                      <Search className="w-4 h-4 mt-0.5 shrink-0" />
                      <span>No templates yet. Fill the form and save one above.</span>
                    </div>
                  ) : (
                    <ul className="max-h-64 overflow-auto -mx-1 space-y-0.5">
                      {templates.map((tpl) => (
                        <li
                          key={tpl.template_id}
                          className="flex items-center gap-2 px-1 py-1.5 rounded-lg hover:bg-gray-50"
                        >
                          <button
                            type="button"
                            onClick={() => handleLoadTemplate(tpl)}
                            className="flex-1 min-w-0 text-left"
                          >
                            <span className="block text-sm font-medium text-gray-900 truncate">{tpl.name}</span>
                            <span className="block text-xs text-gray-400 truncate">
                              {categoryName(tpl.category || tpl.payload?.category) || '—'}
                              {tpl.created_by_name ? ` · ${tpl.created_by_name}` : ''}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteTemplate(tpl)}
                            aria-label={`Delete template ${tpl.name}`}
                            className="shrink-0 p-1.5 rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 laptop:grid-cols-[1fr_320px] gap-5 items-start">
        {/* ---- Form column ---- */}
        <div className="space-y-4">
          {/* AUTO-FILL FROM WEB (inline Catalog Autopilot) — collapsed by default
              so the normal manual flow is unchanged. */}
          <div className="card !p-0 overflow-hidden">
            <button
              type="button"
              onClick={() => setAutopilotOpen((v) => !v)}
              className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-gray-50 transition-colors"
              aria-expanded={autopilotOpen ? 'true' : 'false'}
            >
              <span className="flex items-center gap-3">
                <span className="text-bv"><Wand2 className="w-5 h-5" /></span>
                <span>
                  <span className="block font-semibold text-gray-900">Auto-fill from web (Autopilot)</span>
                  <span className="block text-xs text-gray-500">
                    Search a brand + model to pull specs, description &amp; images
                  </span>
                </span>
              </span>
              <ChevronDown className={clsx('w-5 h-5 text-gray-400 transition-transform', autopilotOpen && 'rotate-180')} />
            </button>

            {autopilotOpen && (
              <div className="px-5 pb-5 pt-1 border-t border-gray-100 space-y-4">
                <div className="grid grid-cols-2 tablet:grid-cols-4 laptop:grid-cols-[1.1fr_1.1fr_0.9fr_0.9fr_1.1fr_auto] gap-3 items-end">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Brand</label>
                    <input
                      className="input-field w-full"
                      value={apBrand}
                      onChange={(e) => setApBrand(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void runAutopilotSearch(); } }}
                      placeholder="Ray-Ban"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
                    <input
                      className="input-field w-full"
                      value={apModel}
                      onChange={(e) => setApModel(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void runAutopilotSearch(); } }}
                      placeholder="RB4105"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Colour</label>
                    <input
                      className="input-field w-full"
                      value={apColor}
                      onChange={(e) => setApColor(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void runAutopilotSearch(); } }}
                      placeholder="601/58"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Size</label>
                    <input
                      className="input-field w-full"
                      value={apSize}
                      onChange={(e) => setApSize(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void runAutopilotSearch(); } }}
                      placeholder="52-18-140"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
                    <select
                      title="Search category"
                      className="input-field w-full disabled:bg-gray-50 disabled:text-gray-500"
                      value={apEffectiveCategory}
                      disabled={Boolean(selectedCategory)}
                      onChange={(e) => setApCategory(e.target.value)}
                    >
                      <option value="">Auto-detect</option>
                      {CATEGORIES.map((c) => (
                        <option key={c.code} value={c.code}>{c.name}</option>
                      ))}
                    </select>
                    {selectedCategory && (
                      <p className="text-[11px] text-gray-400 mt-0.5">Using the form's category</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => runAutopilotSearch()}
                    disabled={apLoading}
                    className="btn-primary inline-flex items-center justify-center gap-2 h-[42px]"
                  >
                    {apLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                    Search
                  </button>
                </div>

                {apSearched && apCandidates.length === 0 && (
                  <p className="text-sm text-gray-500 flex items-start gap-2">
                    <Info className="w-4 h-4 mt-0.5 shrink-0" />
                    No candidates found. Try a different model number or spelling, or use the full
                    {' '}
                    <Link to="/catalog/autopilot" className="text-bv underline">Catalog Autopilot</Link>
                    {' '}page (more sources + rights review).
                  </p>
                )}

                {apSearched && apCandidates.length === 1 && (
                  <p className="text-xs text-gray-500 flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    Only 1 source result — refine colour / size / category for more options.
                  </p>
                )}

                {apCandidates.length > 0 && (
                  <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
                    {apCandidates.slice(0, 8).map((c) => (
                      <AutopilotCandidateRow key={c.candidate_id} c={c} onUse={() => { void useAutopilotCandidate(c); }} />
                    ))}
                  </div>
                )}
                {apCandidates.length > 8 && (
                  <p className="text-[11px] text-gray-400">
                    Showing the top 8 of {apCandidates.length} candidates (sorted by confidence).
                  </p>
                )}

                {/* Fill confirmation right where the button was clicked: what
                    just landed in the MAIN form below. */}
                {autoFilled.size > 0 && (
                  <p className="text-sm text-violet-700 bg-violet-50 border border-violet-200 rounded-lg px-3 py-2 flex items-center gap-2">
                    <SparklesIcon className="w-4 h-4 shrink-0" />
                    Filled {autoFilled.size} field{autoFilled.size === 1 ? '' : 's'}
                    {images.length > 0 && <> + {images.length} image{images.length === 1 ? '' : 's'}</>}
                    {apRefUrls[0] && apDomain(apRefUrls[0]) ? ` from ${apDomain(apRefUrls[0])}` : ''}
                    {' '}into the product form below — please verify.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* AUTOPILOT FILL SUMMARY — "operator verifies" strip. Lists how many
              fields/images were auto-filled, where the data came from, and the
              extra (unmapped) specs found so nothing scraped is invisible. */}
          {(autoFilled.size > 0 || Object.keys(apExtras).length > 0 || apRefUrls.length > 0) && (
            <div className="rounded-xl border border-violet-200 bg-violet-50/60 px-4 py-3 text-sm">
              <div className="flex items-start gap-2.5">
                <SparklesIcon className="w-4 h-4 text-violet-600 mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="text-gray-800">
                    Auto-filled <span className="font-semibold">{autoFilled.size}</span> field{autoFilled.size === 1 ? '' : 's'}
                    {images.length > 0 && <> + <span className="font-semibold">{images.length}</span> image{images.length === 1 ? '' : 's'}</>}
                    {apRefUrls.length > 0 && (
                      <>
                        {' '}from{' '}
                        {apRefUrls.map((u, i) => (
                          <a
                            key={u}
                            href={u}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-0.5 text-violet-700 underline decoration-violet-300 hover:decoration-violet-600"
                          >
                            {apDomain(u)}
                            <ExternalLink className="w-3 h-3" />
                            {i < apRefUrls.length - 1 ? ',' : ''}
                          </a>
                        ))}
                      </>
                    )}
                    {' '}— <span className="font-semibold">please verify</span> the marked fields.
                  </p>
                  {apImageCopy && imageRehostSummary(apImageCopy.copied, apImageCopy.kept) && (
                    <p className="text-xs text-gray-600 flex items-center gap-1.5">
                      <ImageIcon className="w-3.5 h-3.5 text-violet-500 shrink-0" />
                      {imageRehostSummary(apImageCopy.copied, apImageCopy.kept)}
                    </p>
                  )}
                  {Object.keys(apExtras).length > 0 && (
                    <details className="text-xs text-gray-600">
                      <summary className="cursor-pointer select-none text-gray-700 font-medium">
                        Extra specs found ({Object.keys(apExtras).length}) — kept on the product
                      </summary>
                      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                        {Object.entries(apExtras).map(([k, v]) => (
                          <span key={k} className="text-gray-600">
                            <span className="text-gray-400">{k}:</span> {v}
                          </span>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => { setAutoFilled(new Set()); setApExtras({}); setApRefUrls([]); setApImageCopy(null); }}
                  className="shrink-0 text-gray-400 hover:text-gray-600"
                  title="Dismiss (clears the auto markers)"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}

          {/* IDENTITY */}
          <Section
            id="identity"
            title="Identity"
            icon={<Tag className="w-5 h-5" />}
            subtitle="Category, brand, model & specs"
          >
            {/* Category picker */}
            <div className="mb-5">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Category <span className="text-red-500">*</span>
              </label>
              <div className="grid grid-cols-3 tablet:grid-cols-4 laptop:grid-cols-6 gap-2">
                {CATEGORIES.map((c) => (
                  <button
                    key={c.code}
                    type="button"
                    onClick={() => setSelectedCategory(c.code)}
                    className={clsx(
                      'flex flex-col items-center gap-1 px-2 py-3 rounded-lg border text-center transition-all',
                      selectedCategory === c.code
                        ? 'border-bv bg-bv-50 ring-1 ring-bv'
                        : 'border-gray-200 hover:border-gray-300'
                    )}
                  >
                    <span className="text-2xl leading-none">{c.icon}</span>
                    <span className="text-xs font-medium text-gray-800">{c.name}</span>
                  </button>
                ))}
              </div>
              {errors.category && (
                <p className="text-red-500 text-xs mt-2">{errors.category}</p>
              )}
            </div>

            {selectedCategory && (
              <>
                {/* Category-specific fields */}
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                  {visibleFields.map((f, i) => renderField(f, i === 0))}
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

                {/* Description */}
                <div className="mt-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
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
                    Advanced — HSN, GST &amp; weight
                    <span className="ml-1 text-xs text-gray-400">(auto-filled from category)</span>
                  </button>

                  {showAdvanced && (
                    <div className="mt-3 space-y-3">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={useAdvancedHSN}
                          onChange={(e) => setUseAdvancedHSN(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300"
                        />
                        <span className="text-gray-600">Use 6-digit HSN (turnover &gt; ₹5 Cr)</span>
                      </label>
                      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            HSN Code <span className="text-red-500">*</span>
                          </label>
                          <select
                            title="HSN Code"
                            value={hsnCode}
                            onChange={(e) => {
                              setHsnCode(e.target.value);
                              const option = getHSNOptions(useAdvancedHSN).find((o) => o.value === e.target.value);
                              if (option) setGstRate(option.gstRate.toString());
                            }}
                            className="input-field w-full"
                          >
                            <option value="">Select HSN Code</option>
                            {getHSNOptions(useAdvancedHSN).map((o) => (
                              <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                          </select>
                          <p className="text-xs text-gray-500 mt-1">Auto-selected based on category</p>
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">GST Rate (%)</label>
                          <input
                            type="text"
                            title="GST Rate (auto-filled from HSN)"
                            value={`${gstRate}%`}
                            readOnly
                            className="input-field w-full bg-gray-50 cursor-not-allowed"
                          />
                          <p className="text-xs text-gray-500 mt-1">Auto-filled from HSN code</p>
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Weight (g)</label>
                          <input
                            type="number"
                            value={weight}
                            onChange={(e) => setWeight(e.target.value)}
                            className="input-field w-full"
                            placeholder="e.g. 50"
                          />
                        </div>
                      </div>

                      {/* GST-compliance note (parity with the Guided wizard). */}
                      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-700">
                        <p>
                          <strong>Note:</strong> {useAdvancedHSN ? '6-digit' : '4-digit'} HSN code is mandatory for GST compliance.
                          {!useAdvancedHSN && ' Use 6-digit HSN if your annual turnover exceeds ₹5 Cr.'}
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </Section>

          {/* PRICING */}
          <Section
            id="pricing"
            title="Pricing"
            icon={<IndianRupee className="w-5 h-5" />}
            subtitle="MRP, offer, cost & discount band"
          >
            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  MRP <span className="text-red-500">*</span>
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
                  <input
                    type="number"
                    value={mrp}
                    onChange={(e) => setMrp(e.target.value)}
                    className="input-field w-full pl-8"
                    placeholder="0.00"
                  />
                </div>
                {errors.mrp && <p className="text-red-500 text-xs mt-1">{errors.mrp}</p>}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Offer Price</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
                  <input
                    type="number"
                    value={offerPrice}
                    onChange={(e) => setOfferPrice(e.target.value)}
                    className="input-field w-full pl-8"
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
                  <label className="block text-sm font-medium text-gray-700 mb-1">Cost Price</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
                    <input
                      type="number"
                      value={costPrice}
                      onChange={(e) => setCostPrice(e.target.value)}
                      className="input-field w-full pl-8"
                      placeholder="Your purchase cost"
                    />
                  </div>
                  {marginPct !== null && (
                    <p className="text-bv text-xs mt-1">Margin: {marginPct}%</p>
                  )}
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Discount Category
                  <span className="text-red-500 ml-1">*</span>
                </label>
                <select
                  title="Discount Category"
                  value={discountCategory}
                  onChange={(e) => setDiscountCategory(e.target.value)}
                  className="input-field w-full"
                >
                  <option value="" disabled>Select a discount tier…</option>
                  <option value="MASS">Mass (Standard discount caps)</option>
                  <option value="PREMIUM">Premium (Reduced discount caps)</option>
                  <option value="LUXURY">Luxury (Minimal discounts)</option>
                </select>
                {errors.discount_category && (
                  <p className="text-red-500 text-xs mt-1">{errors.discount_category}</p>
                )}
              </div>
            </div>
          </Section>

          {/* INVENTORY */}
          <Section
            id="inventory"
            title="Inventory"
            icon={<Boxes className="w-5 h-5" />}
            subtitle="Reorder level (stock, SKU & barcode are automatic)"
          >
            <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reorder Level</label>
                <input
                  type="number"
                  title="Reorder Level"
                  placeholder="5"
                  value={reorderLevel}
                  onChange={(e) => setReorderLevel(e.target.value)}
                  className="input-field w-full"
                  min="0"
                />
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Stock is added via Goods Receipt (GRN), not at product-create time. The SKU is auto-assigned when the
              product is created and our internal barcode is generated at goods receipt — neither is entered here.
              Set the reorder level and you&apos;ll be alerted when stock falls below it.
            </p>

            {/* Product images — real upload (durably stored + served by the
                backend; the create payload sends the resulting URLs). */}
            <div className="mt-4 pt-4 border-t border-gray-100">
              <label className="block text-sm font-medium text-gray-700 mb-2">Product Images</label>

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
                  'border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors',
                  dragActive ? 'border-bv bg-bv-50' : 'border-gray-300 hover:border-gray-400'
                )}
              >
                {uploadingImages ? (
                  <Loader2 className="w-12 h-12 mx-auto text-bv mb-2 animate-spin" />
                ) : (
                  <ImageIcon className="w-12 h-12 mx-auto text-gray-400 mb-2" />
                )}
                <p className="text-gray-500">
                  {uploadingImages ? 'Uploading…' : 'Drag and drop images here, or click to browse'}
                </p>
                <span className="btn-outline mt-4 inline-flex items-center pointer-events-none">
                  <Upload className="w-4 h-4 mr-2" />
                  Upload Images
                </span>
              </div>

              {images.length > 0 && (
                <div className="mt-3 grid grid-cols-3 tablet:grid-cols-4 laptop:grid-cols-6 gap-3">
                  {images.map((url) => (
                    <div key={url} className="relative group aspect-square rounded-lg overflow-hidden border border-gray-200">
                      <img
                        src={url}
                        alt="Product"
                        className="w-full h-full object-cover"
                        onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = '0.3'; }}
                      />
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); removeImage(url); }}
                        aria-label="Remove image"
                        title="Remove image"
                        className="absolute top-1 right-1 p-1 rounded-full bg-white/90 text-gray-600 hover:text-red-600 shadow"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Section>

          {/* ONLINE (collapsed by default) */}
          <Section
            id="online"
            title="Online"
            icon={<Globe className="w-5 h-5" />}
            subtitle="Shopify sync flags"
          >
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
              <div>
                <p className="font-medium text-gray-900">Sync to Shopify</p>
                <p className="text-sm text-gray-500">Push this product to Shopify (future vendor channel)</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  title="Sync to Shopify"
                  aria-label="Sync to Shopify"
                  checked={syncToShopify}
                  onChange={(e) => setSyncToShopify(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bv"></div>
              </label>
            </div>

            {syncToShopify && (
              <div className="mt-4 space-y-4">
                <div className="flex items-center justify-between p-3 border rounded-lg">
                  <div>
                    <p className="font-medium text-gray-900">Point of Sale</p>
                    <p className="text-sm text-gray-500">Publish to Shopify POS</p>
                  </div>
                  <input
                    type="checkbox"
                    title="Publish to Shopify POS"
                    aria-label="Publish to Shopify POS"
                    checked={publishPOS}
                    onChange={(e) => setPublishPOS(e.target.checked)}
                    className="w-5 h-5 rounded border-gray-300"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
                  <input
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
                    <div className="flex flex-wrap gap-2 mt-2">
                      {shopifyTags.map((tag) => (
                        <span key={tag} className="inline-flex items-center px-2 py-1 text-sm bg-gray-100 rounded-full">
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
              </div>
            )}
          </Section>

          {/* Action bar (mobile / inline fallback — rail also has buttons) */}
          <div className="flex flex-wrap items-center gap-3 laptop:hidden">
            <button
              type="button"
              onClick={() => handleSubmit(false)}
              disabled={isSubmitting}
              className="btn-primary flex items-center gap-2"
            >
              {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save product
            </button>
            <button
              type="button"
              onClick={() => handleSubmit(true)}
              disabled={isSubmitting}
              className="btn-secondary flex items-center gap-2"
            >
              <RotateCcw className="w-4 h-4" />
              Save + New
            </button>
          </div>
        </div>

        {/* ---- Live Review rail ---- */}
        <aside className="laptop:sticky laptop:top-4 space-y-4">
          <div className="card">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-bv" />
              <h3 className="font-semibold text-gray-900">Review</h3>
            </div>

            <dl className="space-y-2 text-sm">
              <ReviewRow label="Category" value={categoryName(selectedCategory) || '—'} />
              <ReviewRow label="Brand" value={attributes.brand_name || '—'} />
              <ReviewRow
                label="Model"
                value={attributes.model_no || attributes.model_name || '—'}
              />
              {/* Remaining filled category attributes (parity with the Guided
                  wizard's review, which listed every filled attribute). */}
              {Object.entries(attributes)
                .filter(([k, v]) => v && !['brand_name', 'model_no', 'model_name'].includes(k))
                .map(([k, v]) => (
                  <ReviewRow key={k} label={k.replace(/_/g, ' ')} value={v} />
                ))}
              <ReviewRow label="MRP" value={mrp ? `₹${mrp}` : '—'} />
              <ReviewRow
                label="Offer"
                value={offerPrice ? `₹${offerPrice}` : mrp ? `₹${mrp} (= MRP)` : '—'}
              />
              {canSeeCost && costPrice && <ReviewRow label="Cost" value={`₹${costPrice}`} />}
              {weight && <ReviewRow label="Weight" value={`${weight} g`} />}
              <ReviewRow label="HSN / GST" value={selectedCategory ? `${hsnCode || '—'} · ${gstRate}%` : '—'} />
              <ReviewRow label="Discount band" value={discountCategory || '—'} />
              <ReviewRow label="Reorder level" value={reorderLevel || '—'} />
              {images.length > 0 && (
                <ReviewRow label="Images" value={`${images.length} uploaded`} />
              )}
              {syncToShopify && <ReviewRow label="Shopify" value="Will sync" />}
            </dl>

            <div className="mt-4 pt-4 border-t border-gray-100 hidden laptop:flex flex-col gap-2">
              <button
                type="button"
                onClick={() => handleSubmit(false)}
                disabled={isSubmitting}
                className="btn-primary w-full flex items-center justify-center gap-2"
              >
                {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Save product
              </button>
              <button
                type="button"
                onClick={() => handleSubmit(true)}
                disabled={isSubmitting}
                className="btn-secondary w-full flex items-center justify-center gap-2"
              >
                <RotateCcw className="w-4 h-4" />
                Save + New
              </button>
            </div>
          </div>

          {/* Keyboard hint */}
          <div className="card !py-3">
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Keyboard className="w-4 h-4" />
              <span className="font-medium text-gray-700">Shortcuts</span>
            </div>
            <div className="mt-2 space-y-1 text-xs text-gray-600">
              <div className="flex items-center justify-between">
                <span>Save</span>
                <span><kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd></span>
              </div>
              <div className="flex items-center justify-between">
                <span>Save + New</span>
                <span><kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Shift</kbd>+<kbd className="qa-kbd">Enter</kbd></span>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

// Review rail row.
function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <dl className="flex items-center justify-between gap-3">
      <dt className="text-gray-500 shrink-0">{label}</dt>
      <dd className="font-medium text-gray-900 text-right truncate">{value}</dd>
    </dl>
  );
}

// ---- Inline Autopilot candidate helpers (mirrors CatalogAutopilotPage) ------

// Human label + AI/authorized flags for a candidate's source badge.
function apSourceBadge(c: AutopilotCandidate): { label: string; ai: boolean; authorized: boolean } {
  const authorized = c.source_class === 'AUTHORIZED';
  if (c.source === AI_ENRICH_SOURCE) return { label: 'AI-suggested', ai: true, authorized };
  if (c.source === 'internal_bvi') return { label: 'Catalog', ai: false, authorized };
  if (c.source === 'brand_site' || c.source === 'myluxottica') return { label: 'Brand site', ai: false, authorized };
  if (c.source === 'marketplace') return { label: 'Web (unverified)', ai: false, authorized };
  return { label: authorized ? 'Authorized' : 'Unverified', ai: false, authorized };
}

// Confidence (or match score) as a 0-100 int; null when neither is present.
function apConfidencePct(c: AutopilotCandidate): number | null {
  const v = c.confidence ?? c.score;
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return null;
  return Math.round(Number(v) * 100);
}

// Hostname (minus www.) for a reference chip / fill summary; '' when invalid.
function apDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// A compact candidate CARD for the inline panel's two-column grid: thumbnail,
// source + confidence badges, a clickable source-reference chip (domain), the
// title/brand/model, and a single "Use this" that fills the whole form.
function AutopilotCandidateRow({ c, onUse }: { c: AutopilotCandidate; onUse: () => void }) {
  const badge = apSourceBadge(c);
  const pct = apConfidencePct(c);
  const refs = candidateReferences(c);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-gray-200 p-3 min-w-0">
      <div className="flex items-start gap-2.5 min-w-0">
        {c.image_urls && c.image_urls.length > 0 && (
          <img
            src={c.image_urls[0]}
            alt={c.title || `${c.brand ?? ''} ${c.model ?? ''}`.trim()}
            className={clsx('w-12 h-12 rounded-lg object-cover border shrink-0', badge.authorized ? 'border-gray-200' : 'border-amber-300')}
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        )}
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-900 text-sm truncate">{c.title || `${c.brand} ${c.model}`}</p>
          <p className="text-xs text-gray-500 truncate">
            {c.brand} · {c.model}{c.color ? ` · ${c.color}` : ''}{c.size ? ` · ${c.size}` : ''}{c.category ? ` · ${c.category}` : ''}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className={clsx('inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] font-medium',
          badge.ai ? 'bg-violet-100 text-violet-700'
            : badge.authorized ? 'bg-emerald-100 text-emerald-700'
            : 'bg-amber-100 text-amber-700')}>
          {badge.ai ? <SparklesIcon className="w-3 h-3" />
            : badge.authorized ? <ShieldCheck className="w-3 h-3" />
            : <AlertTriangle className="w-3 h-3" />}
          {badge.label}
        </span>
        {pct !== null && (
          <span className={clsx('inline-flex items-center gap-1 text-[11px] font-semibold px-1.5 py-0.5 rounded-full',
            pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 70 ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-600')}>
            <Gauge className="w-3 h-3" />
            {pct}%
          </span>
        )}
        {/* Source-reference chip: the exact page this data came from. */}
        {refs.slice(0, 2).map((r) => (
          <a
            key={r.url}
            href={r.url}
            target="_blank"
            rel="noopener noreferrer"
            title={r.url}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] bg-gray-100 text-gray-600 hover:bg-gray-200 hover:text-gray-800 max-w-[160px]"
          >
            <span className="truncate">{r.domain}</span>
            <ExternalLink className="w-2.5 h-2.5 shrink-0" />
          </a>
        ))}
      </div>
      {/* THE action (owner requirement): unmistakably push everything —
          category, every mapped field, description, HSN/GST, images — into
          the MAIN product form. Full-width primary, not a subtle link. */}
      <button
        type="button"
        onClick={onUse}
        className="w-full px-3 py-2 rounded-md bg-bv text-white text-sm font-semibold inline-flex items-center justify-center gap-1.5 hover:opacity-90"
      >
        <PackagePlus className="w-4 h-4" /> Fill product form
      </button>
    </div>
  );
}

export default QuickAddPage;
