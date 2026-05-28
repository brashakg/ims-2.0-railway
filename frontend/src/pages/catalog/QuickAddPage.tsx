// ============================================================================
// IMS 2.0 - Quick Add (fast, one-screen product create)
// ============================================================================
// Phase A of the product-add redesign. Collapses the 6-step Guided wizard into
// ONE scrollable, keyboard-first screen WITHOUT dropping any field:
//   - Accordion sections: Identity / Pricing / Inventory / Online
//   - Smart defaults (category -> HSN/GST auto) collapsed under "Advanced"
//   - Live Review summary rail on the right
//   - Ctrl+Enter = Save ; Ctrl+Shift+Enter = Save + New (keeps category + brand)
// Shares CATEGORY_FIELDS + the create payload mapping with the wizard via
// productAddShared.ts so the two modes are field- and contract-identical.

import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
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
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { productApi } from '../../services/api/products';
import { getHSNOptions } from '../../constants/gst';
import {
  CATEGORIES,
  CATEGORY_FIELDS,
  categoryName,
  validateProductForm,
  buildProductPayload,
  resolveHsnGst,
  type CategoryField,
  type ProductFormValues,
} from './productAddShared';
import clsx from 'clsx';

type SectionId = 'identity' | 'pricing' | 'inventory' | 'online';

export function QuickAddPage() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

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
  const [discountCategory, setDiscountCategory] = useState('MASS');

  // Inventory
  const [initialQuantity, setInitialQuantity] = useState('0');
  const [barcode, setBarcode] = useState('');
  const [reorderLevel, setReorderLevel] = useState('5');

  // Online (Shopify)
  const [syncToShopify, setSyncToShopify] = useState(false);
  const [shopifyTags, setShopifyTags] = useState<string[]>([]);
  const [publishPOS, setPublishPOS] = useState(true);

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

  const firstFieldRef = useRef<HTMLSelectElement | HTMLInputElement | null>(null);

  // Auto-fill HSN + GST when category (or 4/6-digit toggle) changes — same
  // behaviour as the wizard's useEffect.
  useEffect(() => {
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
    }),
    [
      selectedCategory, attributes, description, hsnCode, gstRate, weight, mrp,
      offerPrice, costPrice, discountCategory, syncToShopify, shopifyTags, publishPOS,
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
      setDiscountCategory('MASS');
      setInitialQuantity('0');
      setBarcode('');
      setReorderLevel('5');
      setSyncToShopify(false);
      setShopifyTags([]);
      setPublishPOS(true);
      setErrors({});
    },
    [attributes.brand_name]
  );

  const handleSubmit = useCallback(
    async (saveAndNew: boolean) => {
      const values = currentValues();
      const newErrors = validateProductForm(values);
      setErrors(newErrors);
      if (Object.keys(newErrors).length > 0) {
        // Make sure the section holding the first error is open.
        if (newErrors.mrp || newErrors.offer_price) {
          setOpenSections((s) => ({ ...s, pricing: true }));
        }
        if (newErrors.category || Object.keys(newErrors).some((k) => k !== 'mrp' && k !== 'offer_price')) {
          setOpenSections((s) => ({ ...s, identity: true }));
        }
        toast.error('Please fix the highlighted fields.');
        return;
      }

      setIsSubmitting(true);
      try {
        await productApi.createProduct(buildProductPayload(values));
        toast.success('Product created successfully!');
        if (saveAndNew) {
          resetForm(true);
          // Keep focus flowing — jump back to the top of the form.
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          navigate('/catalog/inventory');
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

  const fields: CategoryField[] = selectedCategory ? CATEGORY_FIELDS[selectedCategory] || [] : [];
  const isLens = selectedCategory === 'LS';
  // The lens stock-power fields are entered via the Power Grid, not here.
  const lensPowerFields = new Set(['sph', 'cyl', 'axis', 'add']);
  const visibleFields = isLens ? fields.filter((f) => !lensPowerFields.has(f.name)) : fields;

  const setAttr = (name: string, value: string) =>
    setAttributes((prev) => ({ ...prev, [name]: value }));

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
  const renderField = (field: CategoryField, autoFocus = false) => (
    <div key={field.name}>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {field.label}
        {field.required && <span className="text-red-500 ml-1">*</span>}
      </label>
      {field.type === 'select' ? (
        <select
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          className="input-field w-full"
        >
          <option value="">Select {field.label}</option>
          {field.options?.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : (
        <input
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          type={field.type}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          placeholder={field.placeholder}
          className="input-field w-full"
        />
      )}
      {errors[field.name] && (
        <p className="text-red-500 text-xs mt-1">{errors[field.name]}</p>
      )}
    </div>
  );

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
          aria-expanded={open}
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
          <div className="eyebrow" style={{ marginBottom: 6 }}>Catalog · Add product</div>
          <h1>One screen. One SKU. Fast.</h1>
          <div className="hint">
            Fill the essentials and hit <kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd> to save.
            Category sets HSN + GST automatically.
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 laptop:grid-cols-[1fr_320px] gap-5 items-start">
        {/* ---- Form column ---- */}
        <div className="space-y-4">
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
                          <label className="block text-sm font-medium text-gray-700 mb-1">HSN Code</label>
                          <select
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
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">GST Rate (%)</label>
                          <input
                            type="text"
                            value={`${gstRate}%`}
                            readOnly
                            className="input-field w-full bg-gray-50 cursor-not-allowed"
                          />
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

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Discount Category</label>
                <select
                  value={discountCategory}
                  onChange={(e) => setDiscountCategory(e.target.value)}
                  className="input-field w-full"
                >
                  <option value="MASS">Mass (Standard discount caps)</option>
                  <option value="PREMIUM">Premium (Reduced discount caps)</option>
                  <option value="LUXURY">Luxury (Minimal discounts)</option>
                </select>
              </div>
            </div>
          </Section>

          {/* INVENTORY */}
          <Section
            id="inventory"
            title="Inventory"
            icon={<Boxes className="w-5 h-5" />}
            subtitle="Barcode & reorder level (stock added via GRN)"
          >
            <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Initial Quantity</label>
                <input
                  type="number"
                  value={initialQuantity}
                  onChange={(e) => setInitialQuantity(e.target.value)}
                  className="input-field w-full"
                  min="0"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Barcode / SKU</label>
                <input
                  type="text"
                  value={barcode}
                  onChange={(e) => setBarcode(e.target.value)}
                  className="input-field w-full"
                  placeholder="Scan or enter barcode"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reorder Level</label>
                <input
                  type="number"
                  value={reorderLevel}
                  onChange={(e) => setReorderLevel(e.target.value)}
                  className="input-field w-full"
                  min="0"
                />
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Stock is created via GRN, not at product-create time. Barcode &amp; reorder level are saved with the SKU.
            </p>
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
              <ReviewRow label="MRP" value={mrp ? `₹${mrp}` : '—'} />
              <ReviewRow
                label="Offer"
                value={offerPrice ? `₹${offerPrice}` : mrp ? `₹${mrp} (= MRP)` : '—'}
              />
              <ReviewRow label="HSN / GST" value={selectedCategory ? `${hsnCode || '—'} · ${gstRate}%` : '—'} />
              <ReviewRow label="Discount band" value={discountCategory} />
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
    <div className="flex items-center justify-between gap-3">
      <dt className="text-gray-500 shrink-0">{label}</dt>
      <dd className="font-medium text-gray-900 text-right truncate">{value}</dd>
    </div>
  );
}

export default QuickAddPage;
