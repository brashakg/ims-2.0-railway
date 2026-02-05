// ============================================================================
// IMS 2.0 - Add Product Page
// ============================================================================
// Dynamic product creation with category-specific fields

import { useState } from 'react';
import {
  Package,
  ChevronRight,
  Save,
  Upload,
  X,
  DollarSign,
  Boxes,
  Globe,
  Search as SearchIcon,
  Loader2,
  AlertCircle,
  CheckCircle,
  Image as ImageIcon,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

// Product categories with display names
const CATEGORIES = [
  { code: 'SG', name: 'Sunglass', icon: '🕶️' },
  { code: 'FR', name: 'Frame', icon: '👓' },
  { code: 'CL', name: 'Contact Lens', icon: '👁️' },
  { code: 'LS', name: 'Optical Lens', icon: '🔍' },
  { code: 'RG', name: 'Reading Glasses', icon: '📖' },
  { code: 'WT', name: 'Wrist Watch', icon: '⌚' },
  { code: 'CK', name: 'Clock', icon: '🕐' },
  { code: 'HA', name: 'Hearing Aid', icon: '🦻' },
  { code: 'ACC', name: 'Accessories', icon: '🎒' },
  { code: 'SMTSG', name: 'Smart Sunglass', icon: '🥽' },
  { code: 'SMTFR', name: 'Smart Glasses', icon: '🤓' },
  { code: 'SMTWT', name: 'Smart Watch', icon: '⌚' },
];

// Category-specific fields configuration
const CATEGORY_FIELDS: Record<string, Array<{
  name: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'date';
  required: boolean;
  options?: string[];
  placeholder?: string;
}>> = {
  SG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Gucci', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  FR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase', 'John Jacobs'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  CL: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Bausch & Lomb', 'Johnson & Johnson', 'Alcon', 'CooperVision', 'Acuvue'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_name', label: 'Colour Name', type: 'text', required: false },
    { name: 'power', label: 'Power', type: 'text', required: true, placeholder: '-6.00 to +6.00' },
    { name: 'pack', label: 'Pack Size', type: 'select', required: false, options: ['1', '3', '6', '30', '90'] },
    { name: 'expiry_date', label: 'Expiry Date', type: 'date', required: false },
  ],
  LS: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Essilor', 'Zeiss', 'Hoya', 'Crizal', 'Kodak', 'Nikon', 'Rodenstock'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'index', label: 'Index', type: 'select', required: true, options: ['1.50', '1.56', '1.59', '1.60', '1.67', '1.74'] },
    { name: 'coating', label: 'Coating', type: 'select', required: true, options: ['UC', 'HC', 'ARC', 'Blue Cut', 'Photochromic', 'Transitions', 'Polarized'] },
    { name: 'lens_category', label: 'Lens Category', type: 'select', required: false, options: ['Single Vision', 'Bifocal', 'Progressive', 'Office', 'Driving'] },
    { name: 'add_on_1', label: 'Add-On 1', type: 'text', required: false },
    { name: 'add_on_2', label: 'Add-On 2', type: 'text', required: false },
    { name: 'add_on_3', label: 'Add-On 3', type: 'text', required: false },
  ],
  RG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'power', label: 'Power', type: 'select', required: false, options: ['+1.00', '+1.25', '+1.50', '+1.75', '+2.00', '+2.25', '+2.50', '+2.75', '+3.00', '+3.50'] },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  WT: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Titan', 'Fastrack', 'Casio', 'Fossil', 'Timex', 'Sonata', 'HMT'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'dial_colour', label: 'Dial Colour', type: 'text', required: false },
    { name: 'belt_colour', label: 'Belt Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (mm)', type: 'number', required: false },
    { name: 'belt_size', label: 'Belt Size (mm)', type: 'number', required: false },
    { name: 'watch_category', label: 'Watch Category', type: 'select', required: false, options: ['Analog', 'Digital', 'Analog-Digital', 'Chronograph', 'Automatic', 'Quartz'] },
  ],
  CK: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Titan', 'Casio', 'Seiko', 'Ajanta', 'Generic'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'dial_colour', label: 'Dial Colour', type: 'text', required: false },
    { name: 'body_colour', label: 'Body Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (inches)', type: 'number', required: false },
    { name: 'battery_size', label: 'Battery Size', type: 'text', required: false },
    { name: 'clock_category', label: 'Clock Category', type: 'select', required: false, options: ['Wall Clock', 'Table Clock', 'Alarm Clock', 'Desk Clock', 'Decorative'] },
  ],
  HA: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Phonak', 'Signia', 'Widex', 'Oticon', 'ReSound', 'Starkey'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'serial_no', label: 'Serial No', type: 'text', required: false },
    { name: 'machine_capacity', label: 'Machine Capacity', type: 'select', required: false, options: ['Mild', 'Moderate', 'Severe', 'Profound'] },
    { name: 'machine_type', label: 'Machine Type', type: 'select', required: false, options: ['BTE', 'ITE', 'ITC', 'CIC', 'RIC', 'Body Worn'] },
  ],
  ACC: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Generic', 'Ray-Ban', 'Oakley', 'Titan'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'accessory_type', label: 'Accessory Type', type: 'select', required: false, options: ['Case', 'Cloth', 'Chain', 'Nose Pad', 'Temple Tip', 'Screw Kit', 'Spray', 'Other'] },
    { name: 'size', label: 'Size', type: 'text', required: false },
    { name: 'pack', label: 'Pack Size', type: 'number', required: false },
    { name: 'expiry_date', label: 'Expiry Date', type: 'date', required: false },
  ],
  SMTSG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Bose', 'Amazon', 'Meta'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
  SMTFR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Meta', 'Amazon', 'Google'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
  SMTWT: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Apple', 'Samsung', 'Fitbit', 'Garmin', 'Amazfit', 'Noise', 'boAt'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'body_colour', label: 'Body Colour', type: 'text', required: false },
    { name: 'belt_colour', label: 'Belt Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (mm)', type: 'number', required: false },
    { name: 'belt_size', label: 'Belt Size (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
};

type Step = 'category' | 'details' | 'pricing' | 'inventory' | 'shopify' | 'review';

export function AddProductPage() {
  const { hasRole } = useAuth();
  const toast = useToast();

  // Step management
  const [currentStep, setCurrentStep] = useState<Step>('category');

  // Form state
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
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

  // Shopify
  const [syncToShopify, setSyncToShopify] = useState(false);
  const [shopifyTags, setShopifyTags] = useState<string[]>([]);
  const [publishOnlineStore, setPublishOnlineStore] = useState(true);
  const [publishPOS, setPublishPOS] = useState(true);

  // Images
  const [images, _setImages] = useState<string[]>([]);
  // Reserved for future image upload functionality
  void _setImages;

  // UI state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Permissions check
  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);

  if (!canAddProduct) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 mx-auto text-gray-300 mb-4" />
          <h2 className="text-xl font-semibold text-gray-700">Access Denied</h2>
          <p className="text-gray-500">You don't have permission to add products.</p>
        </div>
      </div>
    );
  }

  const steps: { id: Step; label: string; icon: React.ReactNode }[] = [
    { id: 'category', label: 'Category', icon: <Package className="w-4 h-4" /> },
    { id: 'details', label: 'Details', icon: <SearchIcon className="w-4 h-4" /> },
    { id: 'pricing', label: 'Pricing', icon: <DollarSign className="w-4 h-4" /> },
    { id: 'inventory', label: 'Inventory', icon: <Boxes className="w-4 h-4" /> },
    { id: 'shopify', label: 'Shopify', icon: <Globe className="w-4 h-4" /> },
    { id: 'review', label: 'Review', icon: <CheckCircle className="w-4 h-4" /> },
  ];

  const currentStepIndex = steps.findIndex((s) => s.id === currentStep);

  const validateCurrentStep = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (currentStep === 'category' && !selectedCategory) {
      newErrors.category = 'Please select a category';
    }

    if (currentStep === 'details' && selectedCategory) {
      const fields = CATEGORY_FIELDS[selectedCategory] || [];
      fields.forEach((field) => {
        if (field.required && !attributes[field.name]) {
          newErrors[field.name] = `${field.label} is required`;
        }
      });
    }

    if (currentStep === 'pricing') {
      if (!mrp || parseFloat(mrp) <= 0) {
        newErrors.mrp = 'MRP is required and must be greater than 0';
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const goToNextStep = () => {
    if (!validateCurrentStep()) return;

    const nextIndex = currentStepIndex + 1;
    if (nextIndex < steps.length) {
      setCurrentStep(steps[nextIndex].id);
    }
  };

  const goToPreviousStep = () => {
    const prevIndex = currentStepIndex - 1;
    if (prevIndex >= 0) {
      setCurrentStep(steps[prevIndex].id);
    }
  };

  const handleSubmit = async () => {
    if (!validateCurrentStep()) return;

    setIsSubmitting(true);
    try {
      const productData = {
        category: selectedCategory,
        attributes,
        description,
        hsn_code: hsnCode,
        gst_rate: parseFloat(gstRate),
        weight: weight ? parseFloat(weight) : null,
        pricing: {
          mrp: parseFloat(mrp),
          offer_price: offerPrice ? parseFloat(offerPrice) : null,
          cost_price: costPrice ? parseFloat(costPrice) : null,
          discount_category: discountCategory,
        },
        inventory: {
          initial_quantity: parseInt(initialQuantity),
          barcode: barcode || null,
          reorder_level: parseInt(reorderLevel),
        },
        images,
        shopify: syncToShopify ? {
          sync_to_shopify: true,
          shopify_tags: shopifyTags,
          publish_to_online_store: publishOnlineStore,
          publish_to_pos: publishPOS,
        } : null,
      };

      // API call would go here
      console.log('Submitting product:', productData);

      // Simulate API call
      await new Promise((resolve) => setTimeout(resolve, 1500));

      toast.success('Product created successfully!');

      // Reset form
      setSelectedCategory(null);
      setAttributes({});
      setDescription('');
      setMrp('');
      setOfferPrice('');
      setCostPrice('');
      setInitialQuantity('0');
      setCurrentStep('category');
    } catch {
      toast.error('Failed to create product. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderCategoryStep = () => (
    <div className="space-y-6">
      <div className="text-center mb-8">
        <h2 className="text-xl font-semibold text-gray-900">Select Product Category</h2>
        <p className="text-gray-500 mt-1">Choose the category that best describes your product</p>
      </div>

      <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-4">
        {CATEGORIES.map((category) => (
          <button
            key={category.code}
            onClick={() => setSelectedCategory(category.code)}
            className={clsx(
              'p-6 rounded-xl border-2 transition-all text-center hover:shadow-md',
              selectedCategory === category.code
                ? 'border-bv-gold-500 bg-bv-gold-50'
                : 'border-gray-200 hover:border-gray-300'
            )}
          >
            <span className="text-4xl mb-2 block">{category.icon}</span>
            <span className="font-medium text-gray-900">{category.name}</span>
            <span className="text-xs text-gray-500 block mt-1">SKU: {category.code}-XXX</span>
          </button>
        ))}
      </div>

      {errors.category && (
        <p className="text-red-500 text-sm text-center">{errors.category}</p>
      )}
    </div>
  );

  const renderDetailsStep = () => {
    const fields = selectedCategory ? CATEGORY_FIELDS[selectedCategory] || [] : [];

    return (
      <div className="space-y-6">
        <div className="text-center mb-8">
          <h2 className="text-xl font-semibold text-gray-900">Product Details</h2>
          <p className="text-gray-500 mt-1">
            Enter the details for your{' '}
            {CATEGORIES.find((c) => c.code === selectedCategory)?.name}
          </p>
        </div>

        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          {fields.map((field) => (
            <div key={field.name}>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {field.label}
                {field.required && <span className="text-red-500 ml-1">*</span>}
              </label>

              {field.type === 'select' ? (
                <select
                  value={attributes[field.name] || ''}
                  onChange={(e) =>
                    setAttributes({ ...attributes, [field.name]: e.target.value })
                  }
                  className="input-field w-full"
                >
                  <option value="">Select {field.label}</option>
                  {field.options?.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : field.type === 'date' ? (
                <input
                  type="date"
                  value={attributes[field.name] || ''}
                  onChange={(e) =>
                    setAttributes({ ...attributes, [field.name]: e.target.value })
                  }
                  className="input-field w-full"
                />
              ) : (
                <input
                  type={field.type}
                  value={attributes[field.name] || ''}
                  onChange={(e) =>
                    setAttributes({ ...attributes, [field.name]: e.target.value })
                  }
                  placeholder={field.placeholder}
                  className="input-field w-full"
                />
              )}

              {errors[field.name] && (
                <p className="text-red-500 text-xs mt-1">{errors[field.name]}</p>
              )}
            </div>
          ))}
        </div>

        <div className="pt-4 border-t">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Description
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="input-field w-full"
            placeholder="Enter product description..."
          />
        </div>

        <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              HSN Code
            </label>
            <input
              type="text"
              value={hsnCode}
              onChange={(e) => setHsnCode(e.target.value)}
              className="input-field w-full"
              placeholder="e.g., 90049090"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              GST Rate (%)
            </label>
            <select
              value={gstRate}
              onChange={(e) => setGstRate(e.target.value)}
              className="input-field w-full"
            >
              <option value="0">0%</option>
              <option value="5">5%</option>
              <option value="12">12%</option>
              <option value="18">18%</option>
              <option value="28">28%</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Weight (grams)
            </label>
            <input
              type="number"
              value={weight}
              onChange={(e) => setWeight(e.target.value)}
              className="input-field w-full"
              placeholder="e.g., 50"
            />
          </div>
        </div>
      </div>
    );
  };

  const renderPricingStep = () => (
    <div className="space-y-6">
      <div className="text-center mb-8">
        <h2 className="text-xl font-semibold text-gray-900">Pricing Information</h2>
        <p className="text-gray-500 mt-1">Set the pricing for your product</p>
      </div>

      <div className="max-w-lg mx-auto space-y-6">
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
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Offer Price
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
            <input
              type="number"
              value={offerPrice}
              onChange={(e) => setOfferPrice(e.target.value)}
              className="input-field w-full pl-8"
              placeholder="Leave blank for same as MRP"
            />
          </div>
          {offerPrice && mrp && parseFloat(offerPrice) < parseFloat(mrp) && (
            <p className="text-green-600 text-xs mt-1">
              {Math.round(((parseFloat(mrp) - parseFloat(offerPrice)) / parseFloat(mrp)) * 100)}% discount
            </p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Cost Price
          </label>
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
          {costPrice && mrp && (
            <p className="text-blue-600 text-xs mt-1">
              Margin: {Math.round(((parseFloat(mrp) - parseFloat(costPrice)) / parseFloat(mrp)) * 100)}%
            </p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Discount Category
          </label>
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
    </div>
  );

  const renderInventoryStep = () => (
    <div className="space-y-6">
      <div className="text-center mb-8">
        <h2 className="text-xl font-semibold text-gray-900">Inventory Settings</h2>
        <p className="text-gray-500 mt-1">Configure inventory tracking for this product</p>
      </div>

      <div className="max-w-lg mx-auto space-y-6">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Initial Quantity
          </label>
          <input
            type="number"
            value={initialQuantity}
            onChange={(e) => setInitialQuantity(e.target.value)}
            className="input-field w-full"
            min="0"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Barcode / SKU
          </label>
          <input
            type="text"
            value={barcode}
            onChange={(e) => setBarcode(e.target.value)}
            className="input-field w-full"
            placeholder="Scan or enter barcode"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Reorder Level
          </label>
          <input
            type="number"
            value={reorderLevel}
            onChange={(e) => setReorderLevel(e.target.value)}
            className="input-field w-full"
            min="0"
          />
          <p className="text-xs text-gray-500 mt-1">
            You'll be alerted when stock falls below this level
          </p>
        </div>

        <div className="pt-4 border-t">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Product Images
          </label>
          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center">
            <ImageIcon className="w-12 h-12 mx-auto text-gray-400 mb-2" />
            <p className="text-gray-500">Drag and drop images here, or click to browse</p>
            <button className="btn-outline mt-4">
              <Upload className="w-4 h-4 mr-2" />
              Upload Images
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  const renderShopifyStep = () => (
    <div className="space-y-6">
      <div className="text-center mb-8">
        <h2 className="text-xl font-semibold text-gray-900">Shopify Integration</h2>
        <p className="text-gray-500 mt-1">Sync this product to your Shopify store</p>
      </div>

      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
          <div>
            <p className="font-medium text-gray-900">Sync to Shopify</p>
            <p className="text-sm text-gray-500">Automatically create this product in Shopify</p>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={syncToShopify}
              onChange={(e) => setSyncToShopify(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-bv-gold-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bv-gold-500"></div>
          </label>
        </div>

        {syncToShopify && (
          <>
            <div className="flex items-center justify-between p-4 border rounded-lg">
              <div>
                <p className="font-medium text-gray-900">Online Store</p>
                <p className="text-sm text-gray-500">Publish to online store</p>
              </div>
              <input
                type="checkbox"
                checked={publishOnlineStore}
                onChange={(e) => setPublishOnlineStore(e.target.checked)}
                className="w-5 h-5 text-bv-gold-500 border-gray-300 rounded focus:ring-bv-gold-500"
              />
            </div>

            <div className="flex items-center justify-between p-4 border rounded-lg">
              <div>
                <p className="font-medium text-gray-900">Point of Sale</p>
                <p className="text-sm text-gray-500">Publish to Shopify POS</p>
              </div>
              <input
                type="checkbox"
                checked={publishPOS}
                onChange={(e) => setPublishPOS(e.target.checked)}
                className="w-5 h-5 text-bv-gold-500 border-gray-300 rounded focus:ring-bv-gold-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Tags
              </label>
              <input
                type="text"
                className="input-field w-full"
                placeholder="Enter tags separated by commas"
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
                    <span
                      key={tag}
                      className="inline-flex items-center px-2 py-1 text-sm bg-gray-100 rounded-full"
                    >
                      {tag}
                      <button
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
          </>
        )}
      </div>
    </div>
  );

  const renderReviewStep = () => {
    const categoryName = CATEGORIES.find((c) => c.code === selectedCategory)?.name;

    return (
      <div className="space-y-6">
        <div className="text-center mb-8">
          <h2 className="text-xl font-semibold text-gray-900">Review & Create</h2>
          <p className="text-gray-500 mt-1">Review your product details before creating</p>
        </div>

        <div className="max-w-2xl mx-auto space-y-6">
          {/* Category & Attributes */}
          <div className="card">
            <h3 className="font-semibold text-gray-900 mb-4">Product Details</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500">Category:</span>
                <span className="ml-2 font-medium">{categoryName}</span>
              </div>
              {Object.entries(attributes).map(([key, value]) => (
                value && (
                  <div key={key}>
                    <span className="text-gray-500">{key.replace(/_/g, ' ')}:</span>
                    <span className="ml-2 font-medium">{value}</span>
                  </div>
                )
              ))}
            </div>
          </div>

          {/* Pricing */}
          <div className="card">
            <h3 className="font-semibold text-gray-900 mb-4">Pricing</h3>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <span className="text-gray-500">MRP:</span>
                <span className="ml-2 font-medium">₹{mrp}</span>
              </div>
              {offerPrice && (
                <div>
                  <span className="text-gray-500">Offer Price:</span>
                  <span className="ml-2 font-medium">₹{offerPrice}</span>
                </div>
              )}
              {costPrice && (
                <div>
                  <span className="text-gray-500">Cost Price:</span>
                  <span className="ml-2 font-medium">₹{costPrice}</span>
                </div>
              )}
            </div>
          </div>

          {/* Inventory */}
          <div className="card">
            <h3 className="font-semibold text-gray-900 mb-4">Inventory</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500">Initial Quantity:</span>
                <span className="ml-2 font-medium">{initialQuantity}</span>
              </div>
              <div>
                <span className="text-gray-500">Reorder Level:</span>
                <span className="ml-2 font-medium">{reorderLevel}</span>
              </div>
            </div>
          </div>

          {/* Shopify */}
          {syncToShopify && (
            <div className="card bg-green-50 border-green-200">
              <div className="flex items-center gap-2 text-green-700">
                <Globe className="w-5 h-5" />
                <span className="font-medium">Will sync to Shopify</span>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Add Product</h1>
        <p className="text-gray-500">Create a new product in your catalog</p>
      </div>

      {/* Progress Steps */}
      <div className="card">
        <div className="flex items-center justify-between">
          {steps.map((step, index) => (
            <div key={step.id} className="flex items-center">
              <button
                onClick={() => index <= currentStepIndex && setCurrentStep(step.id)}
                className={clsx(
                  'flex items-center gap-2 px-3 py-2 rounded-lg transition-colors',
                  currentStep === step.id
                    ? 'bg-bv-gold-100 text-bv-gold-700'
                    : index < currentStepIndex
                    ? 'text-green-600 hover:bg-green-50'
                    : 'text-gray-400'
                )}
                disabled={index > currentStepIndex}
              >
                {index < currentStepIndex ? (
                  <CheckCircle className="w-4 h-4" />
                ) : (
                  step.icon
                )}
                <span className="hidden tablet:inline font-medium">{step.label}</span>
              </button>
              {index < steps.length - 1 && (
                <ChevronRight className="w-4 h-4 text-gray-300 mx-2" />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Step Content */}
      <div className="card min-h-[400px]">
        {currentStep === 'category' && renderCategoryStep()}
        {currentStep === 'details' && renderDetailsStep()}
        {currentStep === 'pricing' && renderPricingStep()}
        {currentStep === 'inventory' && renderInventoryStep()}
        {currentStep === 'shopify' && renderShopifyStep()}
        {currentStep === 'review' && renderReviewStep()}
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button
          onClick={goToPreviousStep}
          disabled={currentStepIndex === 0}
          className="btn-secondary"
        >
          Previous
        </button>

        {currentStep === 'review' ? (
          <button
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="btn-primary flex items-center gap-2"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Creating...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                Create Product
              </>
            )}
          </button>
        ) : (
          <button onClick={goToNextStep} className="btn-primary">
            Next
            <ChevronRight className="w-4 h-4 ml-1" />
          </button>
        )}
      </div>
    </div>
  );
}

export default AddProductPage;
