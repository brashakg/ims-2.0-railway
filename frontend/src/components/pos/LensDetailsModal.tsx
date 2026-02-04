// ============================================================================
// IMS 2.0 - Lens Details Modal for POS
// ============================================================================
// Step-by-step lens configuration for frames/sunglasses
// Uses real API data from admin lens configuration

import { useState, useEffect } from 'react';
import { X, Eye, ChevronRight, ChevronLeft, Check, AlertCircle } from 'lucide-react';
import { adminLensApi } from '../../services/api';

export interface LensDetails {
  brandId: string;
  brandLabel: string;
  subbrandId: string;
  subbrandLabel: string;
  indexId: string;
  indexLabel: string;
  coatingId: string;
  coatingLabel: string;
  addOns: string[];
  addOnIds: string[];
  lensCategory: 'single-vision' | 'bifocal' | 'progressive';
  basePrice: number;
  totalPrice: number;
  finalPrice: number;
  discountAmount: number;
}

interface LensDetailsModalProps {
  onClose: () => void;
  onSave: (details: LensDetails) => void;
}

interface LensBrand {
  id: string;
  name: string;
  code: string;
  tier?: string;
}

interface LensIndex {
  id: string;
  value: string;
  name: string;
  multiplier: number;
}

interface LensCoating {
  id: string;
  name: string;
  code: string;
  price: number;
}

interface LensAddon {
  id: string;
  name: string;
  code: string;
  price: number;
}

type Step = 'brand' | 'index' | 'coating' | 'addons' | 'summary';

export function LensDetailsModal({ onClose, onSave }: LensDetailsModalProps) {
  const [step, setStep] = useState<Step>('brand');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // API Data
  const [brands, setBrands] = useState<LensBrand[]>([]);
  const [indices, setIndices] = useState<LensIndex[]>([]);
  const [coatings, setCoatings] = useState<LensCoating[]>([]);
  const [addons, setAddons] = useState<LensAddon[]>([]);

  // Selections
  const [selectedBrand, setSelectedBrand] = useState<LensBrand | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<LensIndex | null>(null);
  const [selectedCoating, setSelectedCoating] = useState<LensCoating | null>(null);
  const [selectedAddons, setSelectedAddons] = useState<LensAddon[]>([]);
  const [lensCategory, setLensCategory] = useState<'single-vision' | 'bifocal' | 'progressive'>('single-vision');

  // Pricing
  const [basePrice, setBasePrice] = useState(0);

  // Load data on mount
  useEffect(() => {
    loadLensData();
  }, []);

  const loadLensData = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [brandsRes, indicesRes, coatingsRes, addonsRes] = await Promise.all([
        adminLensApi.getLensBrands().catch(() => ({ brands: [] })),
        adminLensApi.getLensIndices().catch(() => ({ indices: [] })),
        adminLensApi.getLensCoatings().catch(() => ({ coatings: [] })),
        adminLensApi.getLensAddons().catch(() => ({ addons: [] })),
      ]);

      const loadedBrands = brandsRes?.brands || brandsRes || [];
      const loadedIndices = indicesRes?.indices || indicesRes || [];
      const loadedCoatings = coatingsRes?.coatings || coatingsRes || [];
      const loadedAddons = addonsRes?.addons || addonsRes || [];

      setBrands(loadedBrands);
      setIndices(loadedIndices);
      setCoatings(loadedCoatings);
      setAddons(loadedAddons);

      // If no data, show error
      if (loadedBrands.length === 0 && loadedIndices.length === 0) {
        setError('No lens configuration found. Please configure lens brands and options in Settings first.');
      }
    } catch {
      setError('Failed to load lens configuration. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  // Calculate total price
  const calculateTotal = () => {
    let total = basePrice;

    // Apply index multiplier
    if (selectedIndex?.multiplier) {
      total *= selectedIndex.multiplier;
    }

    // Apply lens category multiplier
    if (lensCategory === 'progressive') {
      total *= 2;
    } else if (lensCategory === 'bifocal') {
      total *= 1.5;
    }

    // Add coating price
    if (selectedCoating?.price) {
      total += selectedCoating.price * 2; // Per pair
    }

    // Add addons
    selectedAddons.forEach(addon => {
      total += addon.price * 2; // Per pair
    });

    return Math.round(total);
  };

  const totalPrice = calculateTotal();
  const discountAmount = 0; // Can be modified to add discount logic
  const finalPrice = totalPrice - discountAmount;

  const handleNext = () => {
    const steps: Step[] = ['brand', 'index', 'coating', 'addons', 'summary'];
    const currentIndex = steps.indexOf(step);
    if (currentIndex < steps.length - 1) {
      setStep(steps[currentIndex + 1]);
    }
  };

  const handleBack = () => {
    const steps: Step[] = ['brand', 'index', 'coating', 'addons', 'summary'];
    const currentIndex = steps.indexOf(step);
    if (currentIndex > 0) {
      setStep(steps[currentIndex - 1]);
    }
  };

  const handleSave = () => {
    if (!selectedBrand || !selectedIndex || !selectedCoating) {
      setError('Please complete all required selections');
      return;
    }

    const details: LensDetails = {
      brandId: selectedBrand.id,
      brandLabel: selectedBrand.name,
      subbrandId: '', // Subbrands can be added later
      subbrandLabel: '',
      indexId: selectedIndex.id,
      indexLabel: selectedIndex.value + (selectedIndex.name ? ` (${selectedIndex.name})` : ''),
      coatingId: selectedCoating.id,
      coatingLabel: selectedCoating.name,
      addOns: selectedAddons.map(a => a.name),
      addOnIds: selectedAddons.map(a => a.id),
      lensCategory,
      basePrice,
      totalPrice,
      finalPrice,
      discountAmount,
    };

    onSave(details);
  };

  const toggleAddon = (addon: LensAddon) => {
    setSelectedAddons(prev => {
      const exists = prev.find(a => a.id === addon.id);
      if (exists) {
        return prev.filter(a => a.id !== addon.id);
      }
      return [...prev, addon];
    });
  };

  const formatCurrency = (amount: number) => `₹${amount.toLocaleString('en-IN')}`;

  const getStepNumber = () => {
    const steps: Step[] = ['brand', 'index', 'coating', 'addons', 'summary'];
    return steps.indexOf(step) + 1;
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Eye className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Add Lens</h2>
              <p className="text-xs text-gray-500">Step {getStepNumber()} of 5</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Progress Bar */}
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-100">
          <div className="flex justify-between text-xs text-gray-500">
            {['Brand', 'Index', 'Coating', 'Add-ons', 'Summary'].map((label, i) => (
              <span
                key={label}
                className={i < getStepNumber() ? 'text-bv-red-600 font-medium' : ''}
              >
                {label}
              </span>
            ))}
          </div>
          <div className="mt-1 h-1 bg-gray-200 rounded-full">
            <div
              className="h-full bg-bv-red-600 rounded-full transition-all"
              style={{ width: `${(getStepNumber() / 5) * 100}%` }}
            />
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex items-center justify-center h-48">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-bv-red-600"></div>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-500">
              <AlertCircle className="w-12 h-12 mb-3 text-amber-500" />
              <p className="text-center">{error}</p>
              <button onClick={loadLensData} className="mt-4 text-bv-red-600 hover:underline">
                Retry
              </button>
            </div>
          ) : (
            <>
              {/* Step: Brand */}
              {step === 'brand' && (
                <div>
                  <h3 className="font-medium text-gray-900 mb-4">Select Lens Brand</h3>
                  {brands.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">
                      No lens brands configured. Please add brands in Settings.
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      {brands.map(brand => (
                        <button
                          key={brand.id}
                          onClick={() => setSelectedBrand(brand)}
                          className={`p-4 border rounded-lg text-left transition-all ${
                            selectedBrand?.id === brand.id
                              ? 'border-bv-red-500 bg-bv-red-50 ring-2 ring-bv-red-200'
                              : 'border-gray-200 hover:border-bv-red-300 hover:bg-gray-50'
                          }`}
                        >
                          <p className="font-medium text-gray-900">{brand.name}</p>
                          {brand.tier && (
                            <p className="text-xs text-gray-500 mt-1">{brand.tier}</p>
                          )}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Lens Category */}
                  <div className="mt-6">
                    <h4 className="text-sm font-medium text-gray-700 mb-2">Lens Type</h4>
                    <div className="flex gap-2">
                      {[
                        { id: 'single-vision', label: 'Single Vision' },
                        { id: 'bifocal', label: 'Bifocal' },
                        { id: 'progressive', label: 'Progressive' },
                      ].map(type => (
                        <button
                          key={type.id}
                          onClick={() => setLensCategory(type.id as any)}
                          className={`px-4 py-2 rounded-lg text-sm ${
                            lensCategory === type.id
                              ? 'bg-bv-red-600 text-white'
                              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                          }`}
                        >
                          {type.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Base Price */}
                  <div className="mt-6">
                    <label className="text-sm font-medium text-gray-700 mb-2 block">
                      Base Price (per lens)
                    </label>
                    <input
                      type="number"
                      value={basePrice}
                      onChange={(e) => setBasePrice(parseFloat(e.target.value) || 0)}
                      className="w-32 px-3 py-2 border border-gray-200 rounded-lg"
                      placeholder="0"
                    />
                  </div>
                </div>
              )}

              {/* Step: Index */}
              {step === 'index' && (
                <div>
                  <h3 className="font-medium text-gray-900 mb-4">Select Lens Index</h3>
                  {indices.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">
                      No lens indices configured. Please add indices in Settings.
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      {indices.map(index => (
                        <button
                          key={index.id}
                          onClick={() => setSelectedIndex(index)}
                          className={`p-4 border rounded-lg text-left transition-all ${
                            selectedIndex?.id === index.id
                              ? 'border-bv-red-500 bg-bv-red-50 ring-2 ring-bv-red-200'
                              : 'border-gray-200 hover:border-bv-red-300 hover:bg-gray-50'
                          }`}
                        >
                          <p className="font-semibold text-gray-900">{index.value}</p>
                          <p className="text-sm text-gray-500">{index.name}</p>
                          {index.multiplier !== 1 && (
                            <p className="text-xs text-bv-red-600 mt-1">
                              {index.multiplier}x price
                            </p>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Step: Coating */}
              {step === 'coating' && (
                <div>
                  <h3 className="font-medium text-gray-900 mb-4">Select Coating</h3>
                  {coatings.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">
                      No coatings configured. Please add coatings in Settings.
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      {coatings.map(coating => (
                        <button
                          key={coating.id}
                          onClick={() => setSelectedCoating(coating)}
                          className={`p-4 border rounded-lg text-left transition-all ${
                            selectedCoating?.id === coating.id
                              ? 'border-bv-red-500 bg-bv-red-50 ring-2 ring-bv-red-200'
                              : 'border-gray-200 hover:border-bv-red-300 hover:bg-gray-50'
                          }`}
                        >
                          <p className="font-medium text-gray-900">{coating.name}</p>
                          <p className="text-sm text-bv-red-600">{formatCurrency(coating.price)}/lens</p>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Step: Add-ons */}
              {step === 'addons' && (
                <div>
                  <h3 className="font-medium text-gray-900 mb-4">Select Add-ons (Optional)</h3>
                  {addons.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">
                      No add-ons configured. You can skip this step.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {addons.map(addon => {
                        const isSelected = selectedAddons.some(a => a.id === addon.id);
                        return (
                          <button
                            key={addon.id}
                            onClick={() => toggleAddon(addon)}
                            className={`w-full p-4 border rounded-lg flex items-center justify-between transition-all ${
                              isSelected
                                ? 'border-bv-red-500 bg-bv-red-50'
                                : 'border-gray-200 hover:border-gray-300'
                            }`}
                          >
                            <div className="text-left">
                              <p className="font-medium text-gray-900">{addon.name}</p>
                              <p className="text-sm text-bv-red-600">{formatCurrency(addon.price)}/lens</p>
                            </div>
                            <div className={`w-5 h-5 rounded border-2 flex items-center justify-center ${
                              isSelected ? 'bg-bv-red-600 border-bv-red-600' : 'border-gray-300'
                            }`}>
                              {isSelected && <Check className="w-3 h-3 text-white" />}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Step: Summary */}
              {step === 'summary' && (
                <div>
                  <h3 className="font-medium text-gray-900 mb-4">Order Summary</h3>
                  <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                    <div className="flex justify-between">
                      <span className="text-gray-600">Brand</span>
                      <span className="font-medium">{selectedBrand?.name || '-'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Lens Type</span>
                      <span className="font-medium capitalize">{lensCategory.replace('-', ' ')}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Index</span>
                      <span className="font-medium">{selectedIndex?.value || '-'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-600">Coating</span>
                      <span className="font-medium">{selectedCoating?.name || '-'}</span>
                    </div>
                    {selectedAddons.length > 0 && (
                      <div className="flex justify-between">
                        <span className="text-gray-600">Add-ons</span>
                        <span className="font-medium">{selectedAddons.map(a => a.name).join(', ')}</span>
                      </div>
                    )}
                    <div className="border-t border-gray-200 pt-3 mt-3">
                      <div className="flex justify-between">
                        <span className="text-gray-600">Base Price (pair)</span>
                        <span>{formatCurrency(basePrice * 2)}</span>
                      </div>
                      {selectedIndex?.multiplier && selectedIndex.multiplier !== 1 && (
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-500">Index Multiplier</span>
                          <span>{selectedIndex.multiplier}x</span>
                        </div>
                      )}
                      {lensCategory !== 'single-vision' && (
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-500">
                            {lensCategory === 'progressive' ? 'Progressive' : 'Bifocal'} Multiplier
                          </span>
                          <span>{lensCategory === 'progressive' ? '2x' : '1.5x'}</span>
                        </div>
                      )}
                      {selectedCoating && (
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-500">Coating</span>
                          <span>{formatCurrency(selectedCoating.price * 2)}</span>
                        </div>
                      )}
                      {selectedAddons.map(addon => (
                        <div key={addon.id} className="flex justify-between text-sm">
                          <span className="text-gray-500">{addon.name}</span>
                          <span>{formatCurrency(addon.price * 2)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="border-t border-gray-200 pt-3">
                      <div className="flex justify-between text-lg font-semibold">
                        <span>Total (Pair)</span>
                        <span className="text-bv-red-600">{formatCurrency(finalPrice)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 bg-gray-50 flex justify-between">
          <button
            onClick={step === 'brand' ? onClose : handleBack}
            className="btn-outline flex items-center gap-2"
          >
            <ChevronLeft className="w-4 h-4" />
            {step === 'brand' ? 'Cancel' : 'Back'}
          </button>
          {step === 'summary' ? (
            <button
              onClick={handleSave}
              className="btn-primary flex items-center gap-2"
              disabled={!selectedBrand || !selectedIndex || !selectedCoating}
            >
              <Check className="w-4 h-4" />
              Add to Order
            </button>
          ) : (
            <button
              onClick={handleNext}
              className="btn-primary flex items-center gap-2"
              disabled={
                (step === 'brand' && !selectedBrand) ||
                (step === 'index' && !selectedIndex) ||
                (step === 'coating' && !selectedCoating)
              }
            >
              Next
              <ChevronRight className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default LensDetailsModal;
