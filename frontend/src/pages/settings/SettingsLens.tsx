// ============================================================================
// IMS 2.0 - Settings: Lens Master (Brands, Indices, Coatings, Add-ons)
// ============================================================================

import { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { adminLensApi } from '../../services/api';
import type { LensBrand, LensIndex, LensCoating, LensAddon } from './settingsTypes';

// ============================================================================
// Lens Master Section
// ============================================================================

export function LensMasterSection() {
  const toast = useToast();

  const [lensBrands, setLensBrands] = useState<LensBrand[]>([]);
  const [lensIndices, setLensIndices] = useState<LensIndex[]>([]);
  const [lensCoatings, setLensCoatings] = useState<LensCoating[]>([]);
  const [lensAddons, setLensAddons] = useState<LensAddon[]>([]);

  useEffect(() => {
    loadLensData();
  }, []);

  const loadLensData = async () => {
    try {
      const [brandsRes, indicesRes, coatingsRes, addonsRes] = await Promise.all([
        adminLensApi.getLensBrands().catch(() => ({ brands: [] })),
        adminLensApi.getLensIndices().catch(() => ({ indices: [] })),
        adminLensApi.getLensCoatings().catch(() => ({ coatings: [] })),
        adminLensApi.getLensAddons().catch(() => ({ addons: [] })),
      ]);
      setLensBrands(brandsRes?.brands || brandsRes || []);
      setLensIndices(indicesRes?.indices || indicesRes || []);
      setLensCoatings(coatingsRes?.coatings || coatingsRes || []);
      setLensAddons(addonsRes?.addons || addonsRes || []);
    } catch {
      // Lens API not available
    }
  };

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4">Lens Master</h2>
        <p className="text-sm text-gray-400 mb-6">
          Configure lens brands, indices, coatings, and add-ons for the lens selection workflow in POS.
        </p>

        {/* Lens Brands */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-300">Lens Brands</h3>
            <button
              onClick={async () => {
                const name = prompt('Enter lens brand name:');
                if (name) {
                  try {
                    await adminLensApi.createLensBrand({ name, code: name.toUpperCase().replace(/\s+/g, '_') });
                    toast.success('Lens brand added');
                    loadLensData();
                  } catch {
                    toast.error('Failed to add lens brand');
                  }
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Brand
            </button>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {lensBrands.length === 0 ? (
              <div className="col-span-4 text-center py-4 text-gray-400">
                No lens brands configured. Click "Add Brand" to add one.
              </div>
            ) : (
              lensBrands.map(brand => (
                <div key={brand.id} className="p-3 bg-gray-900 rounded-lg flex items-center justify-between">
                  <span className="text-sm">{brand.name}</span>
                  <div className="flex items-center gap-1">
                    <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                    <Trash2
                      className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                      onClick={async () => {
                        if (window.confirm(`Delete lens brand "${brand.name}"?`)) {
                          try {
                            await adminLensApi.deleteLensBrand(brand.id);
                            toast.success('Lens brand deleted');
                            loadLensData();
                          } catch {
                            toast.error('Failed to delete lens brand');
                          }
                        }
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Lens Indices */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-300">Lens Indices</h3>
            <button
              onClick={async () => {
                const value = prompt('Enter index value (e.g., 1.56):');
                const name = prompt('Enter index name (e.g., Standard):');
                if (value && name) {
                  try {
                    await adminLensApi.createLensIndex({ value, multiplier: 1.0, description: name });
                    toast.success('Lens index added');
                    loadLensData();
                  } catch {
                    toast.error('Failed to add lens index');
                  }
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Index
            </button>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {lensIndices.length === 0 ? (
              <div className="col-span-4 text-center py-4 text-gray-400">
                No lens indices configured. Click "Add Index" to add one.
              </div>
            ) : (
              lensIndices.map(idx => (
                <div key={idx.id} className="p-3 bg-gray-900 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium">{idx.value}</span>
                    <span className="text-xs text-gray-400 ml-2">{idx.name}</span>
                  </div>
                  <Trash2
                    className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                    onClick={async () => {
                      if (window.confirm(`Delete lens index "${idx.value}"?`)) {
                        try {
                          await adminLensApi.deleteLensIndex(idx.id);
                          toast.success('Lens index deleted');
                          loadLensData();
                        } catch {
                          toast.error('Failed to delete lens index');
                        }
                      }
                    }}
                  />
                </div>
              ))
            )}
          </div>
        </div>

        {/* Coatings */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-300">Coatings</h3>
            <button
              onClick={async () => {
                const name = prompt('Enter coating name:');
                const priceStr = prompt('Enter coating price:');
                if (name && priceStr) {
                  try {
                    await adminLensApi.createLensCoating({
                      name,
                      code: name.toUpperCase().replace(/\s+/g, '_'),
                      price: parseFloat(priceStr) || 0,
                    });
                    toast.success('Coating added');
                    loadLensData();
                  } catch {
                    toast.error('Failed to add coating');
                  }
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Coating
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {lensCoatings.length === 0 ? (
              <div className="col-span-3 text-center py-4 text-gray-400">
                No coatings configured. Click "Add Coating" to add one.
              </div>
            ) : (
              lensCoatings.map(coating => (
                <div key={coating.id} className="p-3 bg-gray-900 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm">{coating.name}</span>
                    <span className="text-xs text-gray-400 ml-2">Rs.{Number(coating.price).toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                    <Trash2
                      className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                      onClick={async () => {
                        if (window.confirm(`Delete coating "${coating.name}"?`)) {
                          try {
                            await adminLensApi.deleteLensCoating(coating.id);
                            toast.success('Coating deleted');
                            loadLensData();
                          } catch {
                            toast.error('Failed to delete coating');
                          }
                        }
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Add-ons */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-300">Add-ons</h3>
            <button
              onClick={async () => {
                const name = prompt('Enter add-on name:');
                const priceStr = prompt('Enter add-on price:');
                if (name && priceStr) {
                  try {
                    await adminLensApi.createLensAddon({
                      name,
                      code: name.toUpperCase().replace(/\s+/g, '_'),
                      price: parseFloat(priceStr) || 0,
                      type: 'ADDON',
                    });
                    toast.success('Add-on added');
                    loadLensData();
                  } catch {
                    toast.error('Failed to add add-on');
                  }
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Add-on
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {lensAddons.length === 0 ? (
              <div className="col-span-3 text-center py-4 text-gray-400">
                No add-ons configured. Click "Add Add-on" to add one.
              </div>
            ) : (
              lensAddons.map(addon => (
                <div key={addon.id} className="p-3 bg-gray-900 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm">{addon.name}</span>
                    <span className="text-xs text-gray-400 ml-2">Rs.{Number(addon.price).toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Edit2 className="w-3 h-3 text-gray-400 cursor-pointer hover:text-bv-red-600" />
                    <Trash2
                      className="w-3 h-3 text-gray-400 cursor-pointer hover:text-red-600"
                      onClick={async () => {
                        if (window.confirm(`Delete add-on "${addon.name}"?`)) {
                          try {
                            await adminLensApi.deleteLensAddon(addon.id);
                            toast.success('Add-on deleted');
                            loadLensData();
                          } catch {
                            toast.error('Failed to delete add-on');
                          }
                        }
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
