'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Upload, X, Loader2, AlertTriangle, Search, Package, Plus, Edit3, ArrowRight, Store } from 'lucide-react';
import Link from 'next/link';
import SearchableDropdown from '@/components/SearchableDropdown';
import VariantManager from '@/components/VariantManager';
import { CATEGORIES as CATEGORY_DEFS } from '@/lib/categories';
import {
  generateTitle,
  generateSKU,
  generateSEOTitle,
  generateSEODescription,
  generatePageUrl,
  generateTags,
} from '@/lib/autoGenerate';

interface Attribute {
  id: string;
  name: string;
  options: Array<{ id: string; value: string }>;
}

interface Location {
  id: string;
  name: string;
}

interface FormData {
  category: string;
  brand: string;
  subBrand: string;
  label: string;
  productName: string;
  modelNo: string;
  colorCode: string;
  shape: string;
  frameColor: string;
  templeColor: string;
  frameMaterial: string;
  templeMaterial: string;
  frameType: string;
  frameSize: string;
  bridge: string;
  templeLength: string;
  weight: string;
  lensColour: string;
  tint: string;
  lensMaterial: string;
  lensUSP: string;
  polarization: string;
  uvProtection: string;
  recommendedFor: string;
  instructions: string;
  ingredients: string;
  benefits: string;
  aboutProduct: string;
  gender: string;
  countryOfOrigin: string;
  warranty: string;
  productUSP: string;
  mrp: string;
  gtin: string;
  upc: string;
  images: string[];
  stockByLocation: Record<string, number>;
}

interface DuplicateProduct {
  id: string;
  title: string;
  brand: string;
  modelNo: string;
  colorCode: string;
  frameSize: string;
  category: string;
  status: string;
  mrp: number;
  sku: string;
  image: string | null;
  variants: Array<{
    id: string;
    colorCode: string;
    colorName: string | null;
    frameColor: string | null;
    frameSize: string | null;
    sku: string | null;
    barcode: string | null;
    mrp: number;
    image: string | null;
    locations: Array<{ id: string; locationId: string; locationName: string; quantity: number }>;
    totalStock: number;
  }>;
  variantCount: number;
}

type WizardStage = 'identify' | 'match' | 'new-product' | 'add-variant' | 'update-stock';

export default function NewProductPage() {
  const router = useRouter();
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const userLocationId = (session?.user as any)?.locationId || null;

  const [loading, setLoading] = useState(false);
  const [attributes, setAttributes] = useState<Attribute[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);

  const [productVariants, setProductVariants] = useState<any[]>([]);

  // ── Wizard state (Identify → Match → Branch) ──
  const [stage, setStage] = useState<WizardStage>('identify');
  const [wizardCategory, setWizardCategory] = useState('');
  const [wizardBrand, setWizardBrand] = useState('');
  const [wizardModelNo, setWizardModelNo] = useState('');
  const [matchChecking, setMatchChecking] = useState(false);
  const [matchResults, setMatchResults] = useState<DuplicateProduct[]>([]);
  const [selectedMatch, setSelectedMatch] = useState<DuplicateProduct | null>(null);
  const [matchError, setMatchError] = useState<string | null>(null);

  // "Add variant" sub-form state (used when stage === 'add-variant')
  const [newVariantData, setNewVariantData] = useState({
    colorCode: '',
    colorName: '',
    frameColor: '',
    frameSize: '',
    barcode: '',
    mrp: '',
    stockByLocation: {} as Record<string, number>,
    images: [] as string[],
  });
  const [savingVariant, setSavingVariant] = useState(false);

  // Duplicate detection state
  const [duplicateProducts, setDuplicateProducts] = useState<DuplicateProduct[]>([]);
  const [showDuplicatePopup, setShowDuplicatePopup] = useState(false);
  const [variantMode, setVariantMode] = useState(false); // true = user chose to add as variant
  const [duplicateChecked, setDuplicateChecked] = useState(false);
  const [checkingDuplicate, setCheckingDuplicate] = useState(false);

  const [formData, setFormData] = useState<FormData>({
    category: '',
    brand: '',
    subBrand: '',
    label: '',
    productName: '',
    modelNo: '',
    colorCode: '',
    shape: '',
    frameColor: '',
    templeColor: '',
    frameMaterial: '',
    templeMaterial: '',
    frameType: '',
    frameSize: '',
    bridge: '',
    templeLength: '',
    weight: '',
    lensColour: '',
    tint: '',
    lensMaterial: '',
    lensUSP: '',
    polarization: '',
    uvProtection: '',
    recommendedFor: '',
    instructions: '',
    ingredients: '',
    benefits: '',
    aboutProduct: '',
    gender: '',
    countryOfOrigin: '',
    warranty: '',
    productUSP: '',
    mrp: '',
    gtin: '',
    upc: '',
    images: [],
    stockByLocation: {},
  });

  // Fetch attributes
  useEffect(() => {
    const fetchAttributes = async () => {
      try {
        const res = await fetch('/api/attributes');
        const data: Attribute[] = await res.json();
        setAttributes(data || []);
      } catch (error) {
        console.error('Error fetching attributes:', error);
      }
    };
    fetchAttributes();
  }, []);

  // Fetch locations
  useEffect(() => {
    const fetchLocations = async () => {
      try {
        const res = await fetch('/api/locations');
        const data: Location[] = await res.json();
        setLocations(data || []);
        // Initialize stock by location
        const stock: Record<string, number> = {};
        data.forEach((loc) => {
          stock[loc.id] = 0;
        });
        setFormData((prev) => ({ ...prev, stockByLocation: stock }));
      } catch (error) {
        console.error('Error fetching locations:', error);
      }
    };
    fetchLocations();
  }, []);

  // ── Duplicate detection: check when brand + modelNo are both filled ──
  const checkForDuplicates = useCallback(async (brand: string, modelNo: string) => {
    if (!brand.trim() || !modelNo.trim()) {
      setDuplicateProducts([]);
      setShowDuplicatePopup(false);
      setDuplicateChecked(false);
      setVariantMode(false);
      return;
    }

    setCheckingDuplicate(true);
    try {
      const res = await fetch(
        `/api/products/check-duplicate?brand=${encodeURIComponent(brand)}&modelNo=${encodeURIComponent(modelNo)}`
      );
      const json = await res.json();

      if (json.success && json.found && json.products.length > 0) {
        setDuplicateProducts(json.products);
        setShowDuplicatePopup(true);
        setDuplicateChecked(false);
      } else {
        setDuplicateProducts([]);
        setShowDuplicatePopup(false);
        setDuplicateChecked(true);
        setVariantMode(false);
      }
    } catch (error) {
      console.error('Error checking duplicates:', error);
      setDuplicateChecked(true);
    } finally {
      setCheckingDuplicate(false);
    }
  }, []);

  // Trigger duplicate check when brand or modelNo changes (debounced)
  useEffect(() => {
    if (!formData.brand || !formData.modelNo) return;

    const timer = setTimeout(() => {
      checkForDuplicates(formData.brand, formData.modelNo);
    }, 500); // 500ms debounce

    return () => clearTimeout(timer);
  }, [formData.brand, formData.modelNo, checkForDuplicates]);

  const getAttributeOptions = (attrName: string): string[] => {
    const attr = attributes.find((a) => a.name.toLowerCase() === attrName.toLowerCase());
    return attr?.options.map((opt) => opt.value) || [];
  };

  const handleInputChange = (
    field: keyof FormData,
    value: string | string[] | Record<string, number>
  ) => {
    setFormData((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const handleStockChange = (locationId: string, quantity: number) => {
    setFormData((prev) => ({
      ...prev,
      stockByLocation: {
        ...prev.stockByLocation,
        [locationId]: quantity,
      },
    }));
  };

  // Client-side image compression to reduce upload size
  const compressImage = (file: File, maxSizeMB: number = 4.5): Promise<File> => {
    return new Promise((resolve, reject) => {
      if (file.size <= maxSizeMB * 1024 * 1024) {
        resolve(file);
        return;
      }
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const img = new Image();
      img.onload = () => {
        // Scale down if needed (max 2048px on longest side)
        let { width, height } = img;
        const maxDim = 2048;
        if (width > maxDim || height > maxDim) {
          const ratio = Math.min(maxDim / width, maxDim / height);
          width = Math.round(width * ratio);
          height = Math.round(height * ratio);
        }
        canvas.width = width;
        canvas.height = height;
        ctx?.drawImage(img, 0, 0, width, height);
        canvas.toBlob(
          (blob) => {
            if (blob) {
              resolve(new File([blob], file.name, { type: 'image/jpeg' }));
            } else {
              resolve(file);
            }
          },
          'image/jpeg',
          0.85
        );
      };
      img.onerror = () => reject(new Error('Failed to load image'));
      img.src = URL.createObjectURL(file);
    });
  };

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    // Check file size (5MB limit)
    const MAX_SIZE = 5 * 1024 * 1024;
    for (const file of files) {
      if (file.size > MAX_SIZE * 2) {
        alert(`File "${file.name}" is too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum allowed is 5MB after compression.`);
        return;
      }
    }

    setUploadingImage(true);
    try {
      const uploadedUrls: string[] = [];
      for (const file of files) {
        // Compress image client-side before upload
        const compressedFile = await compressImage(file);
        const formDataObj = new FormData();
        formDataObj.append('file', compressedFile);

        const res = await fetch('/api/images', {
          method: 'POST',
          body: formDataObj,
        });
        const data = await res.json();
        if (data.url) {
          uploadedUrls.push(data.url);
        }
      }
      setFormData((prev) => ({
        ...prev,
        images: [...prev.images, ...uploadedUrls],
      }));
    } catch (error) {
      console.error('Error uploading image:', error);
    } finally {
      setUploadingImage(false);
    }
  };

  const handleRemoveImage = (index: number) => {
    setFormData((prev) => ({
      ...prev,
      images: prev.images.filter((_, i) => i !== index),
    }));
  };

  const handleSubmit = async (
    e: React.FormEvent,
    status: 'DRAFT' | 'PUBLISHED'
  ) => {
    e.preventDefault();
    setLoading(true);

    try {
      const payload: Record<string, any> = {
        category: formData.category,
        brand: formData.brand,
        subBrand: formData.subBrand,
        fullModelNo: formData.modelNo,
        frameSize: formData.frameSize,
        frameColor: formData.frameColor,
        productName: formData.productName,
        modelNo: formData.modelNo,
        colorCode: formData.colorCode,
        shape: formData.shape,
        templeColor: formData.templeColor,
        frameMaterial: formData.frameMaterial,
        templeMaterial: formData.templeMaterial,
        frameType: formData.frameType,
        bridgeWidth: formData.bridge,
        templeLength: formData.templeLength,
        weight: formData.weight,
        lensMaterial: formData.lensMaterial,
        polarization: formData.polarization,
        warranty: formData.warranty,
        gender: formData.gender,
        gtin: formData.gtin,
        upc: formData.upc,
        mrp: parseFloat(formData.mrp || '0'),
        status,
        images: formData.images.map((url) => ({ url })),
        locations: isAdmin
          ? Object.entries(formData.stockByLocation).map(
              ([locationId, quantity]) => ({
                locationId,
                quantity,
              })
            )
          : userLocationId
            ? [{ locationId: userLocationId, quantity: 0 }]
            : [],
      };

      // Include variants if any were added
      if (productVariants.length > 0) {
        payload.variants = productVariants.map((v) => ({
          colorCode: v.colorCode,
          colorName: v.colorName || '',
          frameSize: v.frameSize || '',
          mrp: v.mrp || parseFloat(formData.mrp || '0'),
          lensColour: v.lensColor || '',
          tint: v.tint || '',
          locations: Object.entries(v.stockByLocation || {}).map(
            ([locationId, quantity]) => ({
              locationId,
              quantity: quantity || 0,
            })
          ),
        }));
      }

      const res = await fetch('/api/products', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error('Failed to create product');

      router.push('/dashboard/products');
    } catch (error) {
      console.error('Error creating product:', error);
      alert('Error creating product');
    } finally {
      setLoading(false);
    }
  };

  // Generate preview data
  const preview = {
    title: generateTitle({
      brand: formData.brand,
      subBrand: formData.subBrand,
      fullModelNo: formData.modelNo,
      frameSize: formData.frameSize,
      frameColor: formData.frameColor,
      productName: formData.productName,
      category: formData.category,
    }),
    sku: generateSKU({
      category: formData.category,
      brand: formData.brand,
      modelNo: formData.modelNo,
      frameSize: formData.frameSize,
    }),
    seoTitle: generateSEOTitle({
      brand: formData.brand,
      fullModelNo: formData.modelNo,
      frameSize: formData.frameSize,
      frameColor: formData.frameColor,
      gender: formData.gender,
      category: formData.category,
    }),
    seoDescription: generateSEODescription({
      brand: formData.brand,
      productName: formData.productName,
      shape: formData.shape,
      frameType: formData.frameType,
      frameColor: formData.frameColor,
      templeColor: formData.templeColor,
      frameMaterial: formData.frameMaterial,
    }),
    pageUrl: generatePageUrl({
      brand: formData.brand,
      subBrand: formData.subBrand,
      fullModelNo: formData.modelNo,
      frameSize: formData.frameSize,
      frameColor: formData.frameColor,
      productName: formData.productName,
      category: formData.category,
    }),
    tags: generateTags({
      brand: formData.brand,
      shape: formData.shape,
      frameColor: formData.frameColor,
      frameMaterial: formData.frameMaterial,
      frameType: formData.frameType,
      gender: formData.gender,
      category: formData.category,
      templeColor: formData.templeColor,
      subBrand: formData.subBrand,
    }),
  };

  // ── Wizard actions ──
  const runIdentifyCheck = async () => {
    if (!wizardCategory || !wizardBrand.trim() || !wizardModelNo.trim()) {
      setMatchError('Please fill in category, brand, and model no.');
      return;
    }
    setMatchError(null);
    setMatchChecking(true);
    try {
      const res = await fetch(
        `/api/products/check-duplicate?brand=${encodeURIComponent(wizardBrand.trim())}&modelNo=${encodeURIComponent(wizardModelNo.trim())}`
      );
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Check failed');
      if (data.found && (data.products || []).length > 0) {
        setMatchResults(data.products);
        setSelectedMatch(null);
        setStage('match');
      } else {
        // Pre-fill the full form with the wizard-collected fields and go
        // straight to 'new-product' stage.
        setFormData((prev) => ({
          ...prev,
          category: wizardCategory,
          brand: wizardBrand.trim(),
          modelNo: wizardModelNo.trim(),
        }));
        setStage('new-product');
      }
    } catch (e) {
      setMatchError(e instanceof Error ? e.message : 'Match check failed');
    } finally {
      setMatchChecking(false);
    }
  };

  const chooseCreateAnyway = (match: DuplicateProduct | null) => {
    setFormData((prev) => ({
      ...prev,
      category: wizardCategory || (match?.category ?? ''),
      brand: wizardBrand.trim() || (match?.brand ?? ''),
      modelNo: wizardModelNo.trim() || (match?.modelNo ?? ''),
    }));
    setStage('new-product');
  };

  const chooseAddVariant = (match: DuplicateProduct) => {
    setSelectedMatch(match);
    setNewVariantData({
      colorCode: '',
      colorName: '',
      frameColor: '',
      frameSize: '',
      barcode: '',
      mrp: match.mrp ? String(match.mrp) : '',
      stockByLocation: {},
      images: [],
    });
    setStage('add-variant');
  };

  const chooseUpdateStock = (match: DuplicateProduct) => {
    setSelectedMatch(match);
    setStage('update-stock');
  };

  // Called by the Update Stock UI whenever a quantity input loses focus.
  // Posts to the new instant-save endpoint; returns true on success.
  const saveVariantStock = async (
    variantId: string,
    locationId: string,
    quantity: number
  ): Promise<boolean> => {
    try {
      const res = await fetch(`/api/variants/${variantId}/stock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locationId, quantity }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Stock update failed');
      // Reflect updated quantity in local match state so the UI stays accurate.
      setSelectedMatch((m) => {
        if (!m) return m;
        return {
          ...m,
          variants: m.variants.map((v) =>
            v.id === variantId
              ? {
                  ...v,
                  locations: v.locations.map((l) =>
                    l.locationId === locationId ? { ...l, quantity } : l
                  ),
                  totalStock:
                    v.locations.reduce(
                      (s, l) =>
                        s + (l.locationId === locationId ? quantity : l.quantity),
                      0
                    ),
                }
              : v
          ),
        };
      });
      return true;
    } catch {
      return false;
    }
  };

  const handleCreateVariant = async () => {
    if (!selectedMatch) return;
    if (!newVariantData.colorCode.trim()) {
      setMatchError('Color code is required');
      return;
    }
    setSavingVariant(true);
    setMatchError(null);
    try {
      const res = await fetch(`/api/products/${selectedMatch.id}/variants`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          colorCode: newVariantData.colorCode.trim(),
          colorName: newVariantData.colorName.trim() || null,
          frameColor: newVariantData.frameColor.trim() || null,
          frameSize: newVariantData.frameSize.trim() || null,
          barcode: newVariantData.barcode.trim() || null,
          mrp: newVariantData.mrp ? parseFloat(newVariantData.mrp) : selectedMatch.mrp,
          locations: Object.entries(newVariantData.stockByLocation)
            .filter(([, q]) => q > 0)
            .map(([locationId, quantity]) => ({ locationId, quantity })),
          images: newVariantData.images.map((url) => ({ url, role: 'RAW' })),
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Variant save failed');
      alert('Variant added successfully. Raw images are queued for the designer.');
      router.push(`/dashboard/products/edit/${selectedMatch.id}`);
    } catch (e) {
      setMatchError(e instanceof Error ? e.message : 'Variant save failed');
    } finally {
      setSavingVariant(false);
    }
  };

  // ── Wizard stages (early-return UIs) ──

  if (stage === 'identify') {
    return (
      <div className="p-4 sm:p-6 bg-slate-50 min-h-screen">
        <div className="max-w-2xl mx-auto">
          <div className="mb-6">
            <h1 className="text-2xl sm:text-3xl font-bold text-slate-900">
              Add Product
            </h1>
            <p className="text-sm text-slate-600 mt-1">
              Start by entering the category, brand and model number. We&apos;ll
              check if this product already exists — if it does, you can add a
              new color/size variant or update stock instead of re-entering
              everything.
            </p>
          </div>
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6 space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Product Category
              </label>
              <select
                value={wizardCategory}
                onChange={(e) => setWizardCategory(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              >
                <option value="">Select category…</option>
                {CATEGORY_DEFS.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Brand
              </label>
              <input
                list="wizard-brand-list"
                type="text"
                value={wizardBrand}
                onChange={(e) => setWizardBrand(e.target.value)}
                placeholder="e.g. Ray-Ban"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              />
              <datalist id="wizard-brand-list">
                {getAttributeOptions('brand').map((b) => (
                  <option key={b} value={b} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Model No
              </label>
              <input
                type="text"
                value={wizardModelNo}
                onChange={(e) => setWizardModelNo(e.target.value)}
                placeholder="e.g. RB3025"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              />
            </div>
            {matchError && (
              <div className="p-2 bg-red-50 border border-red-200 rounded text-red-800 text-sm">
                {matchError}
              </div>
            )}
            <button
              onClick={runIdentifyCheck}
              disabled={matchChecking || !wizardCategory || !wizardBrand.trim() || !wizardModelNo.trim()}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
            >
              {matchChecking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {matchChecking ? 'Checking…' : 'Continue'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (stage === 'match') {
    return (
      <div className="p-4 sm:p-6 bg-slate-50 min-h-screen">
        <div className="max-w-3xl mx-auto">
          <button
            onClick={() => setStage('identify')}
            className="text-sm text-slate-600 hover:text-slate-900 mb-4"
          >
            ← Back
          </button>
          <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 mb-2">
            Match found
          </h1>
          <p className="text-sm text-slate-600 mb-4">
            A product with brand <b>{wizardBrand}</b> and model <b>{wizardModelNo}</b> already exists. What do you want to do?
          </p>
          <div className="space-y-3">
            {matchResults.map((m) => (
              <div
                key={m.id}
                className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden"
              >
                <div className="p-4 flex items-start gap-3 border-b border-slate-100">
                  {m.image ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={m.image} alt={m.title || ''} className="w-14 h-14 rounded object-cover border border-slate-200" />
                  ) : (
                    <div className="w-14 h-14 rounded bg-slate-100 border border-slate-200 flex items-center justify-center">
                      <Package className="w-6 h-6 text-slate-400" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-slate-900 truncate">
                      {m.title || `${m.brand} ${m.modelNo}`}
                    </h3>
                    <div className="text-xs text-slate-500">
                      {m.category} · {m.brand}
                      {m.modelNo ? ` · ${m.modelNo}` : ''} · MRP ₹{m.mrp || 0}
                    </div>
                  </div>
                </div>

                {m.variants.length > 0 && (
                  <div className="divide-y divide-slate-100 text-sm">
                    <div className="px-4 py-2 bg-slate-50 text-xs font-medium text-slate-500 uppercase tracking-wider">
                      Existing variants ({m.variants.length})
                    </div>
                    {m.variants.map((v) => (
                      <div key={v.id} className="px-4 py-2 flex items-center gap-3">
                        <span className="font-medium text-slate-900">
                          {v.colorName || v.colorCode}
                          {v.frameSize ? ` / ${v.frameSize}` : ''}
                        </span>
                        <span className="text-xs text-slate-500 ml-auto">
                          <Store className="w-3 h-3 inline mr-1" />
                          {v.totalStock} in stock
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="p-3 flex flex-wrap gap-2 bg-slate-50 border-t border-slate-100">
                  <button
                    onClick={() => chooseAddVariant(m)}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm rounded bg-emerald-600 text-white hover:bg-emerald-700"
                  >
                    <Plus className="w-4 h-4" />
                    Add new color/size variant
                  </button>
                  <button
                    onClick={() => chooseUpdateStock(m)}
                    disabled={m.variants.length === 0}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                  >
                    <Edit3 className="w-4 h-4" />
                    Update stock on existing variants
                  </button>
                  <Link
                    href={`/dashboard/products/edit/${m.id}`}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                  >
                    <ArrowRight className="w-4 h-4" />
                    Open full edit
                  </Link>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 text-center">
            <button
              onClick={() => chooseCreateAnyway(null)}
              className="text-xs text-slate-500 hover:text-slate-700 underline"
            >
              I know what I&apos;m doing — create a separate product anyway
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (stage === 'add-variant' && selectedMatch) {
    const parent = selectedMatch;
    return (
      <div className="p-4 sm:p-6 bg-slate-50 min-h-screen">
        <div className="max-w-2xl mx-auto">
          <button onClick={() => setStage('match')} className="text-sm text-slate-600 hover:text-slate-900 mb-4">
            ← Back
          </button>
          <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 mb-1">
            Add variant to {parent.brand} {parent.modelNo}
          </h1>
          <p className="text-sm text-slate-600 mb-4">
            Only color + size fields and stock — everything else inherits from the parent product.
          </p>
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6 space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Color Code *</label>
                <input
                  type="text"
                  value={newVariantData.colorCode}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, colorCode: e.target.value }))}
                  placeholder="e.g. 001, BLK"
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Color Name</label>
                <input
                  type="text"
                  value={newVariantData.colorName}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, colorName: e.target.value }))}
                  placeholder="e.g. Gold, Black"
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Frame Color</label>
                <input
                  type="text"
                  value={newVariantData.frameColor}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, frameColor: e.target.value }))}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Size</label>
                <input
                  type="text"
                  value={newVariantData.frameSize}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, frameSize: e.target.value }))}
                  placeholder="e.g. 55, 58, S, M, L"
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Barcode</label>
                <input
                  type="text"
                  value={newVariantData.barcode}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, barcode: e.target.value }))}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  MRP <span className="text-slate-400">(defaults to ₹{parent.mrp || 0})</span>
                </label>
                <input
                  type="number"
                  value={newVariantData.mrp}
                  onChange={(e) => setNewVariantData((d) => ({ ...d, mrp: e.target.value }))}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
                />
              </div>
            </div>

            {locations.length > 0 && (
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Stock by location</label>
                <div className="grid grid-cols-2 gap-2">
                  {locations.map((loc) => (
                    <div key={loc.id} className="flex items-center gap-2 text-sm">
                      <span className="text-slate-700 truncate flex-1">{loc.name}</span>
                      <input
                        type="number"
                        min={0}
                        value={newVariantData.stockByLocation[loc.id] ?? 0}
                        onChange={(e) =>
                          setNewVariantData((d) => ({
                            ...d,
                            stockByLocation: { ...d.stockByLocation, [loc.id]: Number(e.target.value) },
                          }))
                        }
                        className="w-24 px-2 py-1 border border-slate-300 rounded text-sm text-right"
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {matchError && (
              <div className="p-2 bg-red-50 border border-red-200 rounded text-red-800 text-sm">{matchError}</div>
            )}

            <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded p-2">
              Raw variant images upload in the existing full-edit flow. Use
              <Link href={`/dashboard/products/edit/${parent.id}`} className="text-blue-600 hover:underline mx-1">full edit</Link>
              to upload variant-specific photos — they go through the Design Queue.
            </div>

            <div className="flex gap-2 pt-2">
              <button
                onClick={handleCreateVariant}
                disabled={savingVariant || !newVariantData.colorCode.trim()}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
              >
                {savingVariant ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Save variant
              </button>
              <button
                type="button"
                onClick={() => setStage('match')}
                className="px-4 py-2.5 rounded-lg border border-slate-300 text-slate-700 bg-white hover:bg-slate-50 text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (stage === 'update-stock' && selectedMatch) {
    const parent = selectedMatch;
    const allLocationIds = Array.from(
      new Set(parent.variants.flatMap((v) => v.locations.map((l) => l.locationId)))
    );
    const locationNamesById = new Map(
      parent.variants
        .flatMap((v) => v.locations)
        .map((l) => [l.locationId, l.locationName])
    );
    return (
      <div className="p-4 sm:p-6 bg-slate-50 min-h-screen">
        <div className="max-w-5xl mx-auto">
          <button onClick={() => setStage('match')} className="text-sm text-slate-600 hover:text-slate-900 mb-4">
            ← Back
          </button>
          <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 mb-1">
            Update stock — {parent.brand} {parent.modelNo}
          </h1>
          <p className="text-sm text-slate-600 mb-4">
            Edit any quantity cell. Changes save instantly when you tab out of the cell.
          </p>
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-slate-600">Variant</th>
                  {allLocationIds.map((lid) => (
                    <th key={lid} className="px-3 py-2 text-right font-medium text-slate-600">
                      {locationNamesById.get(lid)}
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right font-medium text-slate-600">Total</th>
                </tr>
              </thead>
              <tbody>
                {parent.variants.map((v) => (
                  <tr key={v.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2 font-medium text-slate-900">
                      {v.colorName || v.colorCode}
                      {v.frameSize ? ` / ${v.frameSize}` : ''}
                      {v.sku && <span className="text-xs text-slate-400 ml-2">{v.sku}</span>}
                    </td>
                    {allLocationIds.map((lid) => {
                      const row = v.locations.find((l) => l.locationId === lid);
                      return (
                        <td key={lid} className="px-3 py-2">
                          <StockCell
                            initialQty={row?.quantity ?? 0}
                            onSave={(qty) => saveVariantStock(v.id, lid, qty)}
                          />
                        </td>
                      );
                    })}
                    <td className="px-3 py-2 text-right font-medium text-slate-900">{v.totalStock}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    );
  }

  // Fall-through: stage === 'new-product' → render the existing full form.
  return (
    <div className="p-4 sm:p-6 bg-gray-50 min-h-screen">
      <div className="max-w-7xl mx-auto">
        <button
          onClick={() => setStage('identify')}
          className="text-sm text-slate-600 hover:text-slate-900 mb-4"
        >
          ← Back to match check
        </button>
        <h1 className="text-3xl font-bold text-gray-900 mb-6">Add Product</h1>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6">
          {/* Form */}
          <form onSubmit={(e) => handleSubmit(e, 'DRAFT')} className="lg:col-span-2">
            <div className="space-y-4">
              {/* Category Selector */}
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                <h2 className="text-lg font-semibold text-gray-900 mb-4">
                  Product Type
                </h2>
                <div className="flex gap-2 flex-wrap">
                  {CATEGORY_DEFS.map((cat) => (
                    <button
                      key={cat.key}
                      type="button"
                      onClick={() => handleInputChange('category', cat.key)}
                      className={`px-4 py-2 rounded-lg font-medium transition-colors text-sm ${
                        formData.category === cat.key
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-900 hover:bg-gray-200'
                      }`}
                    >
                      {cat.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Section 1: Brand & Identity */}
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                <h3 className="text-base font-semibold text-gray-900 mb-4">
                  Brand & Identity
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <SearchableDropdown
                    label="Brand"
                    options={getAttributeOptions('brand')}
                    value={formData.brand}
                    onChange={(val) => handleInputChange('brand', val)}
                  />
                  <SearchableDropdown
                    label="Sub-Brand"
                    options={getAttributeOptions('subbrand')}
                    value={formData.subBrand}
                    onChange={(val) => handleInputChange('subBrand', val)}
                  />
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Label
                    </label>
                    <input
                      type="text"
                      value={formData.label}
                      onChange={(e) => handleInputChange('label', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Product Name
                    </label>
                    <input
                      type="text"
                      value={formData.productName}
                      onChange={(e) => handleInputChange('productName', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Model No
                    </label>
                    <input
                      type="text"
                      value={formData.modelNo}
                      onChange={(e) => handleInputChange('modelNo', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                </div>
                <p className="mt-4 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded p-2">
                  Color, size, bridge, temple length, weight, lens colour and
                  tint are now set <b>per variant</b>. Scroll down to the
                  &ldquo;Variants&rdquo; section to add one or more variants.
                </p>
              </div>

              {/* Section 2: Frame Attributes (product-level only) */}
              {formData.category !== 'SOLUTIONS' && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Frame Attributes
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                    <SearchableDropdown
                      label="Shape"
                      options={getAttributeOptions('shape')}
                      value={formData.shape}
                      onChange={(val) => handleInputChange('shape', val)}
                    />
                    <SearchableDropdown
                      label="Frame Material"
                      options={getAttributeOptions('framematerial')}
                      value={formData.frameMaterial}
                      onChange={(val) => handleInputChange('frameMaterial', val)}
                    />
                    <SearchableDropdown
                      label="Temple Material"
                      options={getAttributeOptions('templematerial')}
                      value={formData.templeMaterial}
                      onChange={(val) => handleInputChange('templeMaterial', val)}
                    />
                    <SearchableDropdown
                      label="Frame Type"
                      options={getAttributeOptions('frametype')}
                      value={formData.frameType}
                      onChange={(val) => handleInputChange('frameType', val)}
                    />
                  </div>
                </div>
              )}

              {/* Section 4: Lens Attributes (product-level only) */}
              {formData.category === 'SUNGLASSES' && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Lens Attributes
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                    <SearchableDropdown
                      label="Lens Material"
                      options={getAttributeOptions('lensmaterial')}
                      value={formData.lensMaterial}
                      onChange={(val) => handleInputChange('lensMaterial', val)}
                    />
                    <SearchableDropdown
                      label="Lens USP"
                      options={getAttributeOptions('lensUSP')}
                      value={formData.lensUSP}
                      onChange={(val) => handleInputChange('lensUSP', val)}
                    />
                    <SearchableDropdown
                      label="Polarization"
                      options={getAttributeOptions('polarization')}
                      value={formData.polarization}
                      onChange={(val) => handleInputChange('polarization', val)}
                    />
                    <SearchableDropdown
                      label="UV Protection"
                      options={getAttributeOptions('uvprotection')}
                      value={formData.uvProtection}
                      onChange={(val) => handleInputChange('uvProtection', val)}
                    />
                  </div>
                </div>
              )}

              {/* Section 5: Solutions Info */}
              {formData.category === 'SOLUTIONS' && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Solutions Info
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Recommended For
                      </label>
                      <textarea
                        value={formData.recommendedFor}
                        onChange={(e) => handleInputChange('recommendedFor', e.target.value)}
                        rows={3}
                        className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Instructions
                      </label>
                      <textarea
                        value={formData.instructions}
                        onChange={(e) => handleInputChange('instructions', e.target.value)}
                        rows={3}
                        className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Ingredients
                      </label>
                      <textarea
                        value={formData.ingredients}
                        onChange={(e) => handleInputChange('ingredients', e.target.value)}
                        rows={3}
                        className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Benefits
                      </label>
                      <textarea
                        value={formData.benefits}
                        onChange={(e) => handleInputChange('benefits', e.target.value)}
                        rows={3}
                        className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        About Product
                      </label>
                      <textarea
                        value={formData.aboutProduct}
                        onChange={(e) => handleInputChange('aboutProduct', e.target.value)}
                        rows={3}
                        className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* Section 6: General Info */}
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                <h3 className="text-base font-semibold text-gray-900 mb-4">
                  General Info
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <SearchableDropdown
                    label="Gender"
                    options={getAttributeOptions('gender')}
                    value={formData.gender}
                    onChange={(val) => handleInputChange('gender', val)}
                  />
                  <SearchableDropdown
                    label="Country of Origin"
                    options={getAttributeOptions('countryoforigin')}
                    value={formData.countryOfOrigin}
                    onChange={(val) => handleInputChange('countryOfOrigin', val)}
                  />
                  <SearchableDropdown
                    label="Warranty"
                    options={getAttributeOptions('warranty')}
                    value={formData.warranty}
                    onChange={(val) => handleInputChange('warranty', val)}
                  />
                  <SearchableDropdown
                    label="Product USP"
                    options={getAttributeOptions('productusp')}
                    value={formData.productUSP}
                    onChange={(val) => handleInputChange('productUSP', val)}
                  />
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      MRP (₹)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={formData.mrp}
                      onChange={(e) => handleInputChange('mrp', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      GTIN
                    </label>
                    <input
                      type="text"
                      value={formData.gtin}
                      onChange={(e) => handleInputChange('gtin', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      UPC
                    </label>
                    <input
                      type="text"
                      value={formData.upc}
                      onChange={(e) => handleInputChange('upc', e.target.value)}
                      className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                    />
                  </div>
                </div>
              </div>

              {/* Section 7: Stock by Location (ADMIN only) */}
              {isAdmin ? (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Stock by Location
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                    {locations.map((loc) => (
                      <div key={loc.id}>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          {loc.name}
                        </label>
                        <input
                          type="number"
                          min="0"
                          value={formData.stockByLocation[loc.id] || 0}
                          onChange={(e) =>
                            handleStockChange(loc.id, parseInt(e.target.value) || 0)
                          }
                          className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                    <p className="text-sm text-blue-800">
                      Product will be automatically assigned to your location: <strong>{locations.find(l => l.id === userLocationId)?.name || 'Default'}</strong>
                    </p>
                  </div>
                </div>
              )}

              {/* Section 8: Duplicate Detection Popup */}
              {showDuplicatePopup && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <div className="bg-amber-50 border border-amber-300 rounded-lg p-5">
                    <div className="flex items-start gap-3">
                      <AlertTriangle className="w-6 h-6 text-amber-600 flex-shrink-0 mt-0.5" />
                      <div className="flex-1">
                        <h3 className="text-base font-semibold text-amber-900 mb-2">
                          Existing Product Found
                        </h3>
                        <p className="text-sm text-amber-800 mb-4">
                          A product with brand <strong>{formData.brand}</strong> and model <strong>{formData.modelNo}</strong> already exists.
                          Would you like to add this as a <strong>variant</strong> (different color/size) or create a <strong>separate product</strong>?
                        </p>

                        {/* Show existing product details */}
                        <div className="space-y-2 mb-4">
                          {duplicateProducts.map((dp) => (
                            <div key={dp.id} className="bg-white border border-amber-200 rounded-lg p-3 flex items-center gap-3">
                              {dp.image && (
                                <img src={dp.image} alt={dp.title || ''} className="w-12 h-12 object-cover rounded" />
                              )}
                              <div className="flex-1">
                                <p className="text-sm font-semibold text-gray-900">{dp.title || `${dp.brand} ${dp.modelNo}`}</p>
                                <p className="text-xs text-gray-500">
                                  SKU: {dp.sku} · Status: {dp.status} · MRP: ₹{dp.mrp}
                                  {dp.colorCode && ` · Color: ${dp.colorCode}`}
                                  {dp.frameSize && ` · Size: ${dp.frameSize}`}
                                </p>
                                {dp.variantCount > 0 && (
                                  <p className="text-xs text-blue-600 mt-1">
                                    {dp.variantCount} variant{dp.variantCount !== 1 ? 's' : ''}: {dp.variants.map(v => `${v.colorCode}/${v.frameSize || '-'}`).join(', ')}
                                  </p>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>

                        <div className="flex gap-3">
                          <button
                            type="button"
                            onClick={() => {
                              setVariantMode(true);
                              setShowDuplicatePopup(false);
                              setDuplicateChecked(true);
                            }}
                            className="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
                          >
                            Add as Variant
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setVariantMode(false);
                              setShowDuplicatePopup(false);
                              setDuplicateChecked(true);
                            }}
                            className="px-4 py-2 bg-gray-100 text-gray-700 font-medium rounded-lg hover:bg-gray-200 border border-gray-300 transition-colors"
                          >
                            Create Unique Product
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Checking indicator */}
              {checkingDuplicate && (
                <div className="border-b border-gray-200 p-4">
                  <div className="flex items-center gap-2 text-sm text-gray-500">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Checking for existing products...
                  </div>
                </div>
              )}

              {/* Section 8b: Variant Manager (only if user chose "Add as Variant") */}
              {variantMode && formData.category && formData.category !== 'SOLUTIONS' && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-2">
                    Variants (Color × Size)
                  </h3>
                  <p className="text-xs text-gray-500 mb-4">
                    Add color and size combinations for <strong>{formData.brand} {formData.modelNo}</strong>.
                    Example: Colors 086, 567 and sizes 55, 52 will create 4 variants.
                  </p>
                  <VariantManager
                    productId=""
                    category={formData.category}
                    attributes={attributes}
                    locations={locations}
                    onVariantsChange={setProductVariants}
                  />
                </div>
              )}

              {/* Section 9: Images */}
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                <h3 className="text-base font-semibold text-gray-900 mb-4">
                  Images
                </h3>
                <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-500 transition-colors cursor-pointer">
                  <input
                    type="file"
                    multiple
                    accept="image/*"
                    onChange={handleImageUpload}
                    disabled={uploadingImage}
                    className="hidden"
                    id="image-input"
                  />
                  <label htmlFor="image-input" className="cursor-pointer">
                    {uploadingImage ? (
                      <Loader2 className="w-8 h-8 animate-spin mx-auto text-blue-600 mb-2" />
                    ) : (
                      <Upload className="w-8 h-8 text-gray-400 mx-auto mb-2" />
                    )}
                    <p className="text-sm text-gray-600">
                      Drag and drop images here or click to select
                    </p>
                  </label>
                </div>

                {/* Uploaded Images */}
                {formData.images.length > 0 && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 mt-4">
                    {formData.images.map((url, index) => (
                      <div key={index} className="relative">
                        <img
                          src={url}
                          alt={`Product ${index + 1}`}
                          className="w-full h-32 object-cover rounded-lg"
                        />
                        <button
                          type="button"
                          onClick={() => handleRemoveImage(index)}
                          className="absolute top-2 right-2 bg-red-600 text-white p-1 rounded-full hover:bg-red-700"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Submit Buttons */}
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6 flex gap-4">
                <button
                  type="submit"
                  disabled={loading}
                  className="flex-1 bg-yellow-600 text-white px-4 py-2 rounded-lg hover:bg-yellow-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                  Save as Draft
                </button>
                <button
                  type="button"
                  onClick={(e) => handleSubmit(e, 'PUBLISHED')}
                  disabled={loading}
                  className="flex-1 bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                  Publish to Shopify
                </button>
              </div>
            </div>
          </form>

          {/* Preview Sidebar */}
          <div className="lg:col-span-1">
            <div className="sticky top-6 bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">
                Live Preview
              </h2>

              <div className="space-y-4 text-sm">
                {formData.images[0] && (
                  <div>
                    <img
                      src={formData.images[0]}
                      alt="Preview"
                      className="w-full h-40 object-cover rounded-lg mb-4"
                    />
                  </div>
                )}

                <div>
                  <p className="text-gray-600 text-xs">Auto-generated Title</p>
                  <p className="font-semibold text-gray-900 truncate">
                    {preview.title || 'Product title will appear here'}
                  </p>
                </div>

                <div className="pt-2 border-t border-gray-200">
                  <p className="text-gray-600 text-xs">SKU</p>
                  <p className="font-mono text-gray-900">
                    {preview.sku || 'XX-XXXX-XX'}
                  </p>
                </div>

                {formData.mrp && (
                  <div className="pt-2 border-t border-gray-200">
                    <p className="text-gray-600 text-xs">MRP</p>
                    <p className="text-lg font-bold text-green-600">
                      ₹{formData.mrp}
                    </p>
                  </div>
                )}

                <div className="pt-2 border-t border-gray-200">
                  <p className="text-gray-600 text-xs">SEO Title</p>
                  <p className="text-gray-900 line-clamp-2">
                    {preview.seoTitle || 'SEO title will appear here'}
                  </p>
                </div>

                <div className="pt-2 border-t border-gray-200">
                  <p className="text-gray-600 text-xs">SEO Description</p>
                  <p className="text-gray-900 text-xs line-clamp-3">
                    {preview.seoDescription || 'SEO description will appear here'}
                  </p>
                </div>

                <div className="pt-2 border-t border-gray-200">
                  <p className="text-gray-600 text-xs">Page URL</p>
                  <p className="text-blue-600 text-xs truncate">
                    /products/{preview.pageUrl || 'page-url'}
                  </p>
                </div>

                {preview.tags && (
                  <div className="pt-2 border-t border-gray-200">
                    <p className="text-gray-600 text-xs mb-2">Tags</p>
                    <div className="flex flex-wrap gap-1">
                      {preview.tags.split(', ').slice(0, 4).map((tag) => (
                        <span
                          key={tag}
                          className="bg-blue-100 text-blue-800 px-2 py-1 rounded text-xs"
                        >
                          {tag}
                        </span>
                      ))}
                      {preview.tags.split(', ').length > 4 && (
                        <span className="text-gray-500 text-xs">
                          +{preview.tags.split(', ').length - 4} more
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Instant-save stock cell. Calls onSave when the input loses focus and the
// value has changed; shows a tiny saving/saved flash.
function StockCell({
  initialQty,
  onSave,
}: {
  initialQty: number;
  onSave: (qty: number) => Promise<boolean>;
}) {
  const [qty, setQty] = useState(initialQty);
  const [state, setState] = useState<'idle' | 'saving' | 'ok' | 'err'>('idle');

  const handleBlur = async () => {
    if (qty === initialQty) return;
    setState('saving');
    const ok = await onSave(qty);
    setState(ok ? 'ok' : 'err');
    if (ok) setTimeout(() => setState('idle'), 1200);
  };

  return (
    <div className="flex items-center justify-end gap-1">
      <input
        type="number"
        min={0}
        value={qty}
        onChange={(e) => setQty(Math.max(0, Number(e.target.value) || 0))}
        onBlur={handleBlur}
        className={`w-20 px-2 py-1 border rounded text-sm text-right ${
          state === 'ok'
            ? 'border-emerald-400 bg-emerald-50'
            : state === 'err'
              ? 'border-red-400 bg-red-50'
              : 'border-slate-300'
        }`}
      />
      {state === 'saving' && <Loader2 className="w-3 h-3 animate-spin text-slate-400" />}
    </div>
  );
}
