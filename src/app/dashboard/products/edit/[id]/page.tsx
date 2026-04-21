'use client';

import { useState, useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Upload, X, Loader2 } from 'lucide-react';
import SearchableDropdown from '@/components/SearchableDropdown';
import { CATEGORIES as CATEGORY_DEFS } from '@/lib/categories';
import { isAttrApplicable } from '@/lib/categoryAttributes';
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

interface ProductImage {
  id: string;
  url: string;
  originalUrl: string;
}

interface ProductLocation {
  id: string;
  locationId: string;
  quantity: number;
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

export default function EditProductPage() {
  const router = useRouter();
  const params = useParams();
  const productId = params.id as string;

  const [loading, setLoading] = useState(false);
  const [fetchingProduct, setFetchingProduct] = useState(true);
  const [attributes, setAttributes] = useState<Attribute[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);

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

  // Fetch product data on load
  useEffect(() => {
    const fetchProduct = async () => {
      if (!productId) return;
      setFetchingProduct(true);
      try {
        const res = await fetch(`/api/products/${productId}`);
        const data = await res.json();
        if (data.success && data.data) {
          const product = data.data;
          setFormData({
            category: product.category || '',
            brand: product.brand || '',
            subBrand: product.subBrand || '',
            label: product.label || '',
            productName: product.productName || '',
            modelNo: product.modelNo || '',
            colorCode: product.colorCode || '',
            shape: product.shape || '',
            frameColor: product.frameColor || '',
            templeColor: product.templeColor || '',
            frameMaterial: product.frameMaterial || '',
            templeMaterial: product.templeMaterial || '',
            frameType: product.frameType || '',
            frameSize: product.frameSize || '',
            bridge: product.bridge || '',
            templeLength: product.templeLength || '',
            weight: product.weight || '',
            lensColour: product.lensColour || '',
            tint: product.tint || '',
            lensMaterial: product.lensMaterial || '',
            lensUSP: product.lensUSP || '',
            polarization: product.polarization || '',
            uvProtection: product.uvProtection || '',
            recommendedFor: product.recommendedFor || '',
            instructions: product.instructions || '',
            ingredients: product.ingredients || '',
            benefits: product.benefits || '',
            aboutProduct: product.aboutProduct || '',
            gender: product.gender || '',
            countryOfOrigin: product.countryOfOrigin || '',
            warranty: product.warranty || '',
            productUSP: product.productUSP || '',
            mrp: product.mrp?.toString() || '',
            gtin: product.gtin || '',
            upc: product.upc || '',
            images: product.images?.map((img: ProductImage) => img.url) || [],
            stockByLocation: product.locations
              ? product.locations.reduce(
                  (acc: Record<string, number>, loc: ProductLocation) => {
                    acc[loc.locationId] = loc.quantity;
                    return acc;
                  },
                  {}
                )
              : {},
          });
        }
      } catch (error) {
        console.error('Error fetching product:', error);
        alert('Error loading product');
      } finally {
        setFetchingProduct(false);
      }
    };
    fetchProduct();
  }, [productId]);

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
        // Initialize stock by location if not already populated
        setFormData((prev) => {
          if (Object.keys(prev.stockByLocation).length === 0) {
            const stock: Record<string, number> = {};
            data.forEach((loc) => {
              stock[loc.id] = 0;
            });
            return { ...prev, stockByLocation: stock };
          }
          return prev;
        });
      } catch (error) {
        console.error('Error fetching locations:', error);
      }
    };
    fetchLocations();
  }, []);

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
        alert(
          `File "${file.name}" is too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum allowed is 5MB after compression.`
        );
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

  const handleSubmit = async (e: React.FormEvent, status: 'DRAFT' | 'PUBLISHED') => {
    e.preventDefault();
    setLoading(true);

    try {
      const payload = {
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
        locations: Object.entries(formData.stockByLocation).map(
          ([locationId, quantity]) => ({
            locationId,
            quantity,
          })
        ),
      };

      const res = await fetch(`/api/products/${productId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error('Failed to update product');

      router.push('/dashboard/products');
    } catch (error) {
      console.error('Error updating product:', error);
      alert('Error updating product');
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

  if (fetchingProduct) {
    return (
      <div className="p-4 sm:p-6 bg-gray-50 min-h-screen flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-12 h-12 animate-spin text-blue-600 mx-auto mb-4" />
          <p className="text-gray-600">Loading product...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 bg-gray-50 min-h-screen">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-900 mb-6">Edit Product</h1>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6">
          {/* Form */}
          <form onSubmit={(e) => handleSubmit(e, 'DRAFT')} className="lg:col-span-2">
            <div className="space-y-4">
              {/* Product Type Selector */}
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
                  tint are now edited <b>per variant</b>. Scroll down to the
                  &ldquo;Variants&rdquo; section to manage them.
                </p>
              </div>

              {/* Section 2: Frame Attributes — per-category: hidden for Contact Lenses / Watches / Smartwatches / Accessories. */}
              {(isAttrApplicable('shape', formData.category) ||
                isAttrApplicable('frameMaterial', formData.category) ||
                isAttrApplicable('frameType', formData.category)) && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Frame Attributes
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                    {isAttrApplicable('shape', formData.category) && (
                      <SearchableDropdown
                        label="Shape"
                        options={getAttributeOptions('shape')}
                        value={formData.shape}
                        onChange={(val) => handleInputChange('shape', val)}
                      />
                    )}
                    {isAttrApplicable('frameMaterial', formData.category) && (
                      <SearchableDropdown
                        label="Frame Material"
                        options={getAttributeOptions('framematerial')}
                        value={formData.frameMaterial}
                        onChange={(val) => handleInputChange('frameMaterial', val)}
                      />
                    )}
                    {isAttrApplicable('templeMaterial', formData.category) && (
                      <SearchableDropdown
                        label="Temple Material"
                        options={getAttributeOptions('templematerial')}
                        value={formData.templeMaterial}
                        onChange={(val) => handleInputChange('templeMaterial', val)}
                      />
                    )}
                    {isAttrApplicable('frameType', formData.category) && (
                      <SearchableDropdown
                        label="Frame Type"
                        options={getAttributeOptions('frametype')}
                        value={formData.frameType}
                        onChange={(val) => handleInputChange('frameType', val)}
                      />
                    )}
                  </div>
                </div>
              )}

              {/* Section 4: Lens Attributes — per-category: Sunglasses, Clip-Ons, Smartglasses, Contact Lenses share lens fields via the attribute map. */}
              {(isAttrApplicable('lensMaterial', formData.category) ||
                isAttrApplicable('lensColour', formData.category) ||
                isAttrApplicable('polarization', formData.category) ||
                isAttrApplicable('uvProtection', formData.category)) && (
                <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
                  <h3 className="text-base font-semibold text-gray-900 mb-4">
                    Lens Attributes
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                    {isAttrApplicable('lensMaterial', formData.category) && (
                      <SearchableDropdown
                        label="Lens Material"
                        options={getAttributeOptions('lensmaterial')}
                        value={formData.lensMaterial}
                        onChange={(val) => handleInputChange('lensMaterial', val)}
                      />
                    )}
                    {isAttrApplicable('lensUSP', formData.category) && (
                      <SearchableDropdown
                        label="Lens USP"
                        options={getAttributeOptions('lensUSP')}
                        value={formData.lensUSP}
                        onChange={(val) => handleInputChange('lensUSP', val)}
                      />
                    )}
                    {isAttrApplicable('polarization', formData.category) && (
                      <SearchableDropdown
                        label="Polarization"
                        options={getAttributeOptions('polarization')}
                        value={formData.polarization}
                        onChange={(val) => handleInputChange('polarization', val)}
                      />
                    )}
                    {isAttrApplicable('uvProtection', formData.category) && (
                      <SearchableDropdown
                        label="UV Protection"
                        options={getAttributeOptions('uvprotection')}
                        value={formData.uvProtection}
                        onChange={(val) => handleInputChange('uvProtection', val)}
                      />
                    )}
                  </div>
                </div>
              )}

              {/* Section 5: Contact Lens / Solutions Info — renamed from SOLUTIONS category which no longer exists. */}
              {formData.category === 'CONTACT_LENSES' && (
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

              {/* Section 7: Stock by Location */}
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

              {/* Section 8: Images */}
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
