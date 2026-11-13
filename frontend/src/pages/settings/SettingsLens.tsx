// ============================================================================
// IMS 2.0 - Settings: Lens Master (Brands, Indices, Coatings, Add-ons)
// ============================================================================

import { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { adminLensApi } from '../../services/api';
import { getErrorMessage } from '../../utils/errorHandler';
import type { LensBrand, LensIndex, LensCoating, LensAddon } from './settingsTypes';

// L1-F08: a price must be a plain non-negative rupee amount (up to 2 decimals).
// Anything else (blank, "banana", "12abc", "-5", "1e3") is refused - never
// turned into 0, which made the item free. A deliberate "0" is kept.
export function parseLensPrice(raw: string | null): number | null {
  const t = (raw ?? '').trim();
  return /^\d+(\.\d{1,2})?$/.test(t) ? Number(t) : null;
}

// Icon buttons (were bare SVGs with no role or name).
const ICON_BTN = 'p-1 rounded text-gray-500 hover:bg-gray-200';

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
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Lens Master</h2>
        <p className="text-sm text-gray-500 mb-6">
          Configure lens brands, indices, coatings, and add-ons for the lens selection workflow in POS.
        </p>

        {/* Lens Brands */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-600">Lens Brands</h3>
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
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2">
            {lensBrands.length === 0 ? (
              <div className="col-span-4 text-center py-4 text-gray-500">
                No lens brands configured. Click "Add Brand" to add one.
              </div>
            ) : (
              lensBrands.map(brand => (
                <div key={brand.id} className="p-3 bg-gray-50 border border-gray-200 rounded-lg flex items-center justify-between">
                  <span className="text-sm">{brand.name}</span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Rename lens brand ${brand.name}`}
                      title={`Rename lens brand ${brand.name}`}
                      className={ICON_BTN}
                      onClick={async () => {
                        const name = prompt('Rename lens brand:', brand.name);
                        if (name && name.trim() && name.trim() !== brand.name) {
                          try {
                            await adminLensApi.updateLensBrand(brand.id, { name: name.trim() });
                            toast.success('Lens brand renamed');
                            loadLensData();
                          } catch {
                            toast.error('Failed to rename lens brand');
                          }
                        }
                      }}
                    >
                      <Edit2 className="w-3 h-3" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete lens brand ${brand.name}`}
                      title={`Delete lens brand ${brand.name}`}
                      className={ICON_BTN}
                      onClick={async () => {
                        if (window.confirm(`Delete lens brand "${brand.name}"?`)) {
                          try {
                            await adminLensApi.deleteLensBrand(brand.id);
                            toast.success('Lens brand deleted');
                            loadLensData();
                          } catch (e) {
                            toast.error(getErrorMessage(e, 'Failed to delete lens brand'));
                          }
                        }
                      }}
                    >
                      <Trash2 className="w-3 h-3" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Lens Indices */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-600">Lens Indices</h3>
            <button
              onClick={async () => {
                const value = prompt('Enter index value (e.g., 1.56):')?.trim();
                if (!value) return;
                const name = prompt('Enter index name (e.g., Standard):')?.trim();
                if (name) {
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
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2">
            {lensIndices.length === 0 ? (
              <div className="col-span-4 text-center py-4 text-gray-500">
                No lens indices configured. Click "Add Index" to add one.
              </div>
            ) : (
              lensIndices.map(idx => (
                <div key={idx.id} className="p-3 bg-gray-50 border border-gray-200 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium">{idx.value}</span>
                    <span className="text-xs text-gray-500 ml-2">{idx.name}</span>
                  </div>
                  <button
                    type="button"
                    aria-label={`Delete lens index ${idx.value}`}
                    title={`Delete lens index ${idx.value}`}
                    className={ICON_BTN}
                    onClick={async () => {
                      if (window.confirm(`Delete lens index "${idx.value}"?`)) {
                        try {
                          await adminLensApi.deleteLensIndex(idx.id);
                          toast.success('Lens index deleted');
                          loadLensData();
                        } catch (e) {
                          toast.error(getErrorMessage(e, 'Failed to delete lens index'));
                        }
                      }
                    }}
                  >
                    <Trash2 className="w-3 h-3" aria-hidden="true" />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Coatings */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-600">Coatings</h3>
            <button
              onClick={async () => {
                const name = prompt('Enter coating name:')?.trim();
                if (!name) return;
                const priceStr = prompt('Enter coating price (Rs., e.g. 500 or 0):');
                if (priceStr === null) return;
                const price = parseLensPrice(priceStr);
                if (price === null) {
                  toast.error('Coating not added: enter the price as a number like 500 or 0');
                  return;
                }
                try {
                  await adminLensApi.createLensCoating({
                    name,
                    code: name.toUpperCase().replace(/\s+/g, '_'),
                    price,
                  });
                  toast.success('Coating added');
                  loadLensData();
                } catch {
                  toast.error('Failed to add coating');
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Coating
            </button>
          </div>
          <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
            {lensCoatings.length === 0 ? (
              <div className="col-span-3 text-center py-4 text-gray-500">
                No coatings configured. Click "Add Coating" to add one.
              </div>
            ) : (
              lensCoatings.map(coating => (
                <div key={coating.id} className="p-3 bg-gray-50 border border-gray-200 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm">{coating.name}</span>
                    <span className="text-xs text-gray-500 ml-2">Rs.{Number(coating.price).toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Rename coating ${coating.name}`}
                      title={`Rename coating ${coating.name}`}
                      className={ICON_BTN}
                      onClick={async () => {
                        const name = prompt('Rename coating:', coating.name);
                        if (name && name.trim() && name.trim() !== coating.name) {
                          try {
                            await adminLensApi.updateLensCoating(coating.id, { name: name.trim() });
                            toast.success('Coating renamed');
                            loadLensData();
                          } catch {
                            toast.error('Failed to rename coating');
                          }
                        }
                      }}
                    >
                      <Edit2 className="w-3 h-3" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete coating ${coating.name}`}
                      title={`Delete coating ${coating.name}`}
                      className={ICON_BTN}
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
                    >
                      <Trash2 className="w-3 h-3" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Add-ons */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-600">Add-ons</h3>
            <button
              onClick={async () => {
                const name = prompt('Enter add-on name:')?.trim();
                if (!name) return;
                const priceStr = prompt('Enter add-on price (Rs., e.g. 300 or 0):');
                if (priceStr === null) return;
                const price = parseLensPrice(priceStr);
                if (price === null) {
                  toast.error('Add-on not added: enter the price as a number like 300 or 0');
                  return;
                }
                try {
                  await adminLensApi.createLensAddon({
                    name,
                    code: name.toUpperCase().replace(/\s+/g, '_'),
                    price,
                    type: 'ADDON',
                  });
                  toast.success('Add-on added');
                  loadLensData();
                } catch {
                  toast.error('Failed to add add-on');
                }
              }}
              className="text-sm text-bv-red-600 hover:underline flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              Add Add-on
            </button>
          </div>
          <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
            {lensAddons.length === 0 ? (
              <div className="col-span-3 text-center py-4 text-gray-500">
                No add-ons configured. Click "Add Add-on" to add one.
              </div>
            ) : (
              lensAddons.map(addon => (
                <div key={addon.id} className="p-3 bg-gray-50 border border-gray-200 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-sm">{addon.name}</span>
                    <span className="text-xs text-gray-500 ml-2">Rs.{Number(addon.price).toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Rename add-on ${addon.name}`}
                      title={`Rename add-on ${addon.name}`}
                      className={ICON_BTN}
                      onClick={async () => {
                        const name = prompt('Rename add-on:', addon.name);
                        if (name && name.trim() && name.trim() !== addon.name) {
                          try {
                            await adminLensApi.updateLensAddon(addon.id, { name: name.trim() });
                            toast.success('Add-on renamed');
                            loadLensData();
                          } catch {
                            toast.error('Failed to rename add-on');
                          }
                        }
                      }}
                    >
                      <Edit2 className="w-3 h-3" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete add-on ${addon.name}`}
                      title={`Delete add-on ${addon.name}`}
                      className={ICON_BTN}
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
                    >
                      <Trash2 className="w-3 h-3" aria-hidden="true" />
                    </button>
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
