// ============================================================================
// IMS 2.0 - New Store Setup Wizard
// ============================================================================

import { useState } from 'react';
import { ChevronRight, ChevronLeft, Check, Loader2 } from 'lucide-react';
import clsx from 'clsx';

type WizardStep = 'basic' | 'address' | 'categories' | 'staff' | 'features' | 'inventory' | 'review';

interface WizardData {
  storeName: string;
  storeCode: string;
  managerName: string;
  managerPhone: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  gstNumber: string;
  categories: string[];
  staffAssignment: Record<string, string>;
  featureToggles: Record<string, boolean>;
  openingInventory: Record<string, number>;
}

const STEPS: { id: WizardStep; label: string; description: string }[] = [
  { id: 'basic', label: 'Basic Info', description: 'Store name and manager' },
  { id: 'address', label: 'Address & GST', description: 'Location and tax details' },
  { id: 'categories', label: 'Categories', description: 'Enable product categories' },
  { id: 'staff', label: 'Staff', description: 'Assign store staff' },
  { id: 'features', label: 'Features', description: 'Enable modules' },
  { id: 'inventory', label: 'Inventory', description: 'Opening stock' },
  { id: 'review', label: 'Review', description: 'Verify details' },
];

const CATEGORIES = ['Frames', 'Lenses', 'Sunglasses', 'Accessories', 'Lens Add-ons'];
const FEATURES = ['POS', 'Eye Test', 'Workshop', 'Loyalty', 'Credit Billing'];

export function StoreSetupWizard() {
  const [currentStep, setCurrentStep] = useState<WizardStep>('basic');
  const [isLoading, setIsLoading] = useState(false);
  const [formData, setFormData] = useState<Partial<WizardData>>({
    storeName: '',
    storeCode: '',
    managerName: '',
    managerPhone: '',
    address: '',
    city: '',
    state: '',
    pincode: '',
    gstNumber: '',
    categories: [],
    staffAssignment: {},
    featureToggles: FEATURES.reduce((acc, f) => ({ ...acc, [f]: true }), {}),
    openingInventory: {},
  });

  const currentStepIndex = STEPS.findIndex(s => s.id === currentStep);

  const handleInputChange = (field: string, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  const handleCategoryToggle = (category: string) => {
    setFormData(prev => ({
      ...prev,
      categories: prev.categories?.includes(category)
        ? prev.categories.filter(c => c !== category)
        : [...(prev.categories || []), category],
    }));
  };

  const handleFeatureToggle = (feature: string) => {
    setFormData(prev => ({
      ...prev,
      featureToggles: {
        ...prev.featureToggles,
        [feature]: !prev.featureToggles?.[feature],
      },
    }));
  };

  const handleNext = () => {
    if (currentStepIndex < STEPS.length - 1) {
      setCurrentStep(STEPS[currentStepIndex + 1].id);
    }
  };

  const handlePrev = () => {
    if (currentStepIndex > 0) {
      setCurrentStep(STEPS[currentStepIndex - 1].id);
    }
  };

  const handleComplete = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('ims_token');
      const response = await fetch('/api/v1/stores', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(formData),
      });
      if (!response.ok) {
        throw new Error(`Store creation failed: ${response.statusText}`);
      }
      // Success handling
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      {/* Step Indicator */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          {STEPS.map((step, idx) => (
            <div key={step.id} className="flex items-center">
              <button
                onClick={() => setCurrentStep(step.id)}
                className={clsx(
                  'w-10 h-10 rounded-full font-semibold transition flex items-center justify-center',
                  idx < currentStepIndex
                    ? 'bg-green-600 text-white'
                    : idx === currentStepIndex
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-200 text-gray-500'
                )}
              >
                {idx < currentStepIndex ? <Check className="w-5 h-5" /> : idx + 1}
              </button>
              {idx < STEPS.length - 1 && (
                <div className={clsx(
                  'h-1 flex-1 mx-2 rounded',
                  idx < currentStepIndex ? 'bg-green-600' : 'bg-gray-200'
                )} />
              )}
            </div>
          ))}
        </div>
        <div className="text-center mb-4">
          <h2 className="text-2xl font-bold text-gray-900">{STEPS[currentStepIndex].label}</h2>
          <p className="text-gray-500">{STEPS[currentStepIndex].description}</p>
        </div>
      </div>

      {/* Form Content */}
      <div className="bg-white border border-gray-200 rounded-lg p-8 mb-8">
        {currentStep === 'basic' && (
          <div className="space-y-4">
            <input
              type="text"
              placeholder="Store Name"
              value={formData.storeName || ''}
              onChange={(e) => handleInputChange('storeName', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
            <input
              type="text"
              placeholder="Store Code (e.g., STR-001)"
              value={formData.storeCode || ''}
              onChange={(e) => handleInputChange('storeCode', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
            <input
              type="text"
              placeholder="Manager Name"
              value={formData.managerName || ''}
              onChange={(e) => handleInputChange('managerName', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
            <input
              type="tel"
              placeholder="Manager Phone"
              value={formData.managerPhone || ''}
              onChange={(e) => handleInputChange('managerPhone', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
          </div>
        )}

        {currentStep === 'address' && (
          <div className="space-y-4">
            <input
              type="text"
              placeholder="Street Address"
              value={formData.address || ''}
              onChange={(e) => handleInputChange('address', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
            <div className="grid grid-cols-2 gap-4">
              <input
                type="text"
                placeholder="City"
                value={formData.city || ''}
                onChange={(e) => handleInputChange('city', e.target.value)}
                className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
              />
              <input
                type="text"
                placeholder="State"
                value={formData.state || ''}
                onChange={(e) => handleInputChange('state', e.target.value)}
                className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
              />
            </div>
            <input
              type="text"
              placeholder="Pincode"
              value={formData.pincode || ''}
              onChange={(e) => handleInputChange('pincode', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
            <input
              type="text"
              placeholder="GST Number"
              value={formData.gstNumber || ''}
              onChange={(e) => handleInputChange('gstNumber', e.target.value)}
              className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
          </div>
        )}

        {currentStep === 'categories' && (
          <div className="space-y-4">
            <p className="text-gray-600 text-sm mb-4">Select categories to enable for this store:</p>
            {CATEGORIES.map(cat => (
              <label key={cat} className="flex items-center gap-3 p-3 bg-gray-50 border border-gray-200 rounded cursor-pointer hover:bg-gray-100">
                <input
                  type="checkbox"
                  checked={formData.categories?.includes(cat) || false}
                  onChange={() => handleCategoryToggle(cat)}
                  className="w-4 h-4"
                />
                <span className="text-gray-900">{cat}</span>
              </label>
            ))}
          </div>
        )}

        {currentStep === 'features' && (
          <div className="space-y-4">
            <p className="text-gray-600 text-sm mb-4">Enable features for this store:</p>
            {FEATURES.map(feature => (
              <label key={feature} className="flex items-center gap-3 p-3 bg-gray-50 border border-gray-200 rounded cursor-pointer hover:bg-gray-100">
                <input
                  type="checkbox"
                  checked={formData.featureToggles?.[feature] || false}
                  onChange={() => handleFeatureToggle(feature)}
                  className="w-4 h-4"
                />
                <span className="text-gray-900">{feature}</span>
              </label>
            ))}
          </div>
        )}

        {currentStep === 'review' && (
          <div className="space-y-4 text-gray-600 text-sm">
            <div>
              <p className="text-gray-500">Store Name:</p>
              <p className="text-gray-900">{formData.storeName}</p>
            </div>
            <div>
              <p className="text-gray-500">Location:</p>
              <p className="text-gray-900">{formData.address}, {formData.city}, {formData.state}</p>
            </div>
            <div>
              <p className="text-gray-500">Manager:</p>
              <p className="text-gray-900">{formData.managerName}</p>
            </div>
            <div>
              <p className="text-gray-500">Categories Enabled:</p>
              <p className="text-gray-900">{formData.categories?.join(', ') || 'None'}</p>
            </div>
          </div>
        )}
      </div>

      {/* Navigation Buttons */}
      <div className="flex justify-between">
        <button
          onClick={handlePrev}
          disabled={currentStepIndex === 0}
          className={clsx(
            'flex items-center gap-2 px-6 py-2 rounded-lg font-medium transition',
            currentStepIndex === 0
              ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200 border border-gray-300'
          )}
        >
          <ChevronLeft className="w-4 h-4" />
          Previous
        </button>

        {currentStep === 'review' ? (
          <button
            onClick={handleComplete}
            disabled={isLoading}
            className={clsx(
              'flex items-center gap-2 px-6 py-2 rounded-lg font-medium transition',
              isLoading
                ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                : 'bg-green-600 text-white hover:bg-green-700'
            )}
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Creating Store...
              </>
            ) : (
              <>
                <Check className="w-4 h-4" />
                Activate Store
              </>
            )}
          </button>
        ) : (
          <button
            onClick={handleNext}
            className="flex items-center gap-2 px-6 py-2 rounded-lg font-medium bg-blue-600 text-white hover:bg-blue-700 transition"
          >
            Next
            <ChevronRight className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}
