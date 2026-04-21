'use client';

import { useState, useEffect } from 'react';
import { Plus, Trash2, Check, Lock, Unlock } from 'lucide-react';
import SearchableDropdown from './SearchableDropdown';

// Saved variants lock their inputs so the parent sees a stable list. Editing
// flips back to unlocked so the row shows inputs again. The Save button is
// what locks a row; main Publish button on the parent form sends the whole
// set of saved variants to Shopify.
interface Variant {
  id: string;
  colorCode: string;
  colorName: string;
  frameSize: string;
  mrp: number;
  srp: number;
  stockByLocation: Record<string, number>;
  images?: string[];
  lensColour?: string;
  tint?: string;
  saved?: boolean;
}

interface VariantManagerProps {
  productId: string;
  category: string;
  attributes: Array<{
    id: string;
    name: string;
    options: Array<{ id: string; value: string }>;
  }>;
  locations: Array<{ id: string; name: string }>;
  onVariantsChange?: (variants: Variant[]) => void;
}

// Resolve options from the live AttributeType name. Case-insensitive to cope
// with framecolor/frame_color/FrameColor historic variants — the Attributes
// page seeds canonical lowercase names, but callers may pass either.
function resolveOptions(
  attributes: VariantManagerProps['attributes'],
  name: string
): string[] {
  const target = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  const match = attributes.find(
    (a) => a.name.toLowerCase().replace(/[^a-z0-9]/g, '') === target
  );
  return match?.options.map((o) => o.value) ?? [];
}

export default function VariantManager({
  productId,
  category,
  attributes,
  locations,
  onVariantsChange,
}: VariantManagerProps) {
  const [variants, setVariants] = useState<Variant[]>([]);

  // DB AttributeType names are canonical lowercase without separators:
  //   framecolor, lenscolour (British spelling per DB), tint
  // The old "frame_color"/"lens_color" lookups left these empty.
  const colorNameOptions = resolveOptions(attributes, 'framecolor');
  const lensColorOptions = resolveOptions(attributes, 'lenscolour');
  const tintOptions = resolveOptions(attributes, 'tint');

  const hasLensFields = ['SUNGLASSES', 'CLIP_ON_FRAMES', 'SMARTGLASSES'].includes(
    category
  );

  useEffect(() => {
    onVariantsChange?.(variants);
  }, [variants, onVariantsChange]);

  const addVariant = () => {
    const newVariant: Variant = {
      id: `variant_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      colorCode: '',
      colorName: '',
      frameSize: '',
      mrp: 0,
      srp: 0,
      stockByLocation: Object.fromEntries(locations.map((loc) => [loc.id, 0])),
      images: [],
      ...(hasLensFields && { lensColour: '', tint: '' }),
      saved: false,
    };
    setVariants((prev) => [...prev, newVariant]);
  };

  const updateVariant = (id: string, field: keyof Variant, value: unknown) => {
    setVariants((prev) =>
      prev.map((v) => (v.id === id ? { ...v, [field]: value } : v))
    );
  };

  const updateStock = (id: string, locationId: string, value: number) => {
    setVariants((prev) =>
      prev.map((v) =>
        v.id === id
          ? {
              ...v,
              stockByLocation: { ...v.stockByLocation, [locationId]: value },
            }
          : v
      )
    );
  };

  const deleteVariant = (id: string) => {
    setVariants((prev) => prev.filter((v) => v.id !== id));
  };

  // Saving locks the variant's inputs. Main form's Publish button is what
  // actually pushes to Shopify — saving here just marks the row complete and
  // prevents accidental edits.
  const saveVariant = (id: string) => {
    const v = variants.find((x) => x.id === id);
    if (!v) return;
    if (!v.colorCode.trim()) {
      alert('Colour code is required before saving.');
      return;
    }
    if (!v.mrp || v.mrp <= 0) {
      alert('MRP must be greater than 0 before saving.');
      return;
    }
    updateVariant(id, 'saved', true);
  };

  const unlockVariant = (id: string) => {
    updateVariant(id, 'saved', false);
  };

  return (
    <div className="space-y-4">
      {/* Header: only "Add Variant" now — Quick Add removed per user request.
          Quick Add generated empty rows in bulk which encouraged sloppy
          colour/size data; one-at-a-time + Save enforces deliberate entry. */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-600">
          {variants.length === 0
            ? 'No variants yet — add one to set colour, size, pricing and stock.'
            : `${variants.length} variant${variants.length === 1 ? '' : 's'} · ${variants.filter((v) => v.saved).length} saved`}
        </p>
        <button
          type="button"
          onClick={addVariant}
          className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Add Variant
        </button>
      </div>

      {/* Variant cards — one card per row, full-width inputs so values are
          fully visible. Previous table layout crammed 10+ columns into a
          narrow viewport and clipped every value. */}
      {variants.length === 0 ? (
        <div className="bg-gray-50 border border-dashed border-gray-300 rounded-lg p-8 text-center text-gray-500">
          <p className="mb-1 font-medium">No variants added yet</p>
          <p className="text-sm">Click &ldquo;Add Variant&rdquo; to create one.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {variants.map((variant, idx) => {
            const locked = !!variant.saved;
            return (
              <div
                key={variant.id}
                className={`rounded-lg border p-5 transition-colors ${
                  locked
                    ? 'bg-green-50 border-green-300'
                    : 'bg-white border-slate-200'
                }`}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-gray-700">
                      Variant #{idx + 1}
                    </span>
                    {locked && (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded">
                        <Check className="w-3 h-3" /> Saved
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {locked ? (
                      <button
                        type="button"
                        onClick={() => unlockVariant(variant.id)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
                      >
                        <Unlock className="w-3.5 h-3.5" />
                        Edit
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => saveVariant(variant.id)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-white bg-green-600 rounded-lg hover:bg-green-700"
                      >
                        <Lock className="w-3.5 h-3.5" />
                        Save Variant
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => deleteVariant(variant.id)}
                      className="p-2 text-red-600 hover:bg-red-50 rounded-lg"
                      title="Delete variant"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Inputs are now full-size (min-h-[44px], px-3 py-2.5, base
                    font) so the characters inside are fully visible even on
                    mobile. Previous table crammed px-2 py-1 inputs. */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Colour Code <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={variant.colorCode}
                      onChange={(e) =>
                        updateVariant(variant.id, 'colorCode', e.target.value)
                      }
                      disabled={locked}
                      placeholder="e.g. 086"
                      className="w-full px-3 py-2.5 min-h-[44px] text-base border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-700"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Colour Name
                    </label>
                    <SearchableDropdown
                      options={colorNameOptions}
                      value={variant.colorName}
                      onChange={(val) =>
                        updateVariant(variant.id, 'colorName', val)
                      }
                      placeholder={
                        colorNameOptions.length === 0
                          ? 'Add options in Attributes page'
                          : 'Select colour'
                      }
                      disabled={locked}
                      size="md"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Frame Size
                    </label>
                    <input
                      type="text"
                      value={variant.frameSize}
                      onChange={(e) =>
                        updateVariant(variant.id, 'frameSize', e.target.value)
                      }
                      disabled={locked}
                      placeholder="e.g. 52"
                      className="w-full px-3 py-2.5 min-h-[44px] text-base border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-700"
                    />
                  </div>

                  {hasLensFields && (
                    <>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Lens Colour
                        </label>
                        <SearchableDropdown
                          options={lensColorOptions}
                          value={variant.lensColour || ''}
                          onChange={(val) =>
                            updateVariant(variant.id, 'lensColour', val)
                          }
                          placeholder={
                            lensColorOptions.length === 0
                              ? 'Add options in Attributes page'
                              : 'Select lens colour'
                          }
                          disabled={locked}
                          size="md"
                        />
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Tint
                        </label>
                        <SearchableDropdown
                          options={tintOptions}
                          value={variant.tint || ''}
                          onChange={(val) =>
                            updateVariant(variant.id, 'tint', val)
                          }
                          placeholder={
                            tintOptions.length === 0
                              ? 'Add options in Attributes page'
                              : 'Select tint'
                          }
                          disabled={locked}
                          size="md"
                        />
                      </div>
                    </>
                  )}

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      MRP (₹) <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={variant.mrp || ''}
                      onChange={(e) =>
                        updateVariant(
                          variant.id,
                          'mrp',
                          parseFloat(e.target.value) || 0
                        )
                      }
                      disabled={locked}
                      placeholder="0"
                      className="w-full px-3 py-2.5 min-h-[44px] text-base border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-700"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      SRP (₹)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={variant.srp || ''}
                      onChange={(e) =>
                        updateVariant(
                          variant.id,
                          'srp',
                          parseFloat(e.target.value) || 0
                        )
                      }
                      disabled={locked}
                      placeholder="Auto (MRP × category discount)"
                      className="w-full px-3 py-2.5 min-h-[44px] text-base border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-700"
                    />
                  </div>
                </div>

                {/* Stock by location — displayed as a horizontal grid so each
                    location's input has room to breathe. */}
                {locations.length > 0 && (
                  <div className="mt-4 pt-4 border-t border-gray-200">
                    <p className="text-sm font-medium text-gray-700 mb-2">
                      Stock by Location
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {locations.map((loc) => (
                        <div key={loc.id}>
                          <label className="block text-xs font-medium text-gray-600 mb-1">
                            {loc.name}
                          </label>
                          <input
                            type="number"
                            min="0"
                            value={variant.stockByLocation[loc.id] ?? 0}
                            onChange={(e) =>
                              updateStock(
                                variant.id,
                                loc.id,
                                parseInt(e.target.value) || 0
                              )
                            }
                            disabled={locked}
                            placeholder="0"
                            className="w-full px-3 py-2.5 min-h-[44px] text-base border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-700"
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Summary */}
      {variants.length > 0 && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
          <p className="text-sm text-gray-700">
            <span className="font-semibold text-gray-900">{variants.length}</span>{' '}
            variant{variants.length !== 1 ? 's' : ''} ·{' '}
            <span className="font-semibold text-green-700">
              {variants.filter((v) => v.saved).length}
            </span>{' '}
            saved. Main <strong>Publish to Shopify</strong> button on the product
            form will push all saved variants at once.
          </p>
        </div>
      )}
    </div>
  );
}
