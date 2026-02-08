// ============================================================================
// IMS 2.0 - Quick Filters Saver Component
// ============================================================================
// Save, load, and manage filter presets for quick access

import { useState, useEffect } from 'react';
import { Star, X, Plus, Trash2 } from 'lucide-react';
import clsx from 'clsx';

export interface FilterPreset {
  id: string;
  name: string;
  filters: Record<string, any>;
  isFavorite: boolean;
  createdAt: number;
}

interface QuickFiltersSaverProps {
  currentFilters: Record<string, any>;
  onLoadFilters: (filters: Record<string, any>) => void;
  storageKey: string; // Unique key for localStorage per page
  disabled?: boolean;
}

const MAX_PRESETS = 10;

export function QuickFiltersSaver({
  currentFilters,
  onLoadFilters,
  storageKey,
  disabled = false,
}: QuickFiltersSaverProps) {
  const [presets, setPresets] = useState<FilterPreset[]>([]);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showPresets, setShowPresets] = useState(false);
  const [presetName, setPresetName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);

  // Load presets from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(`filters_${storageKey}`);
    if (stored) {
      try {
        setPresets(JSON.parse(stored));
      } catch (error) {
        console.error('Failed to load filter presets:', error);
      }
    }
  }, [storageKey]);

  // Save presets to localStorage
  const savePresetsToStorage = (newPresets: FilterPreset[]) => {
    localStorage.setItem(`filters_${storageKey}`, JSON.stringify(newPresets));
    setPresets(newPresets);
  };

  const handleSavePreset = () => {
    if (!presetName.trim()) {
      alert('Please enter a preset name');
      return;
    }

    if (presets.length >= MAX_PRESETS && !editingId) {
      alert(`Maximum ${MAX_PRESETS} presets allowed. Delete one to save a new preset.`);
      return;
    }

    const hasActiveFilters = Object.values(currentFilters).some(
      val => val !== undefined && val !== '' && val !== null
    );

    if (!hasActiveFilters) {
      alert('No active filters to save');
      return;
    }

    if (editingId) {
      // Update existing preset
      const updated = presets.map(p =>
        p.id === editingId
          ? { ...p, name: presetName, filters: { ...currentFilters } }
          : p
      );
      savePresetsToStorage(updated);
      setEditingId(null);
    } else {
      // Create new preset
      const newPreset: FilterPreset = {
        id: Date.now().toString(),
        name: presetName,
        filters: { ...currentFilters },
        isFavorite: false,
        createdAt: Date.now(),
      };
      savePresetsToStorage([...presets, newPreset]);
    }

    setPresetName('');
    setShowSaveModal(false);
  };

  const handleLoadPreset = (preset: FilterPreset) => {
    onLoadFilters({ ...preset.filters });
    setShowPresets(false);
  };

  const handleDeletePreset = (id: string) => {
    if (confirm('Delete this filter preset?')) {
      savePresetsToStorage(presets.filter(p => p.id !== id));
    }
  };

  const handleToggleFavorite = (id: string) => {
    const updated = presets.map(p =>
      p.id === id ? { ...p, isFavorite: !p.isFavorite } : p
    );
    savePresetsToStorage(updated);
  };

  const favoritePresets = presets.filter(p => p.isFavorite);
  const otherPresets = presets.filter(p => !p.isFavorite);
  const sortedPresets = [...favoritePresets, ...otherPresets];

  const hasActiveFilters = Object.values(currentFilters).some(
    val => val !== undefined && val !== '' && val !== null
  );

  return (
    <div className="relative">
      {/* Save Preset Button */}
      <button
        onClick={() => {
          setShowSaveModal(true);
          setEditingId(null);
          setPresetName('');
        }}
        disabled={disabled || !hasActiveFilters}
        className={clsx(
          'inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors',
          disabled || !hasActiveFilters
            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
            : 'bg-amber-50 text-amber-700 hover:bg-amber-100 active:bg-amber-200'
        )}
        title="Save current filters as a preset"
        aria-label="Save filter preset"
      >
        <Plus className="w-4 h-4" />
        <span className="text-sm">Save Filters</span>
      </button>

      {/* Load Presets Button */}
      <button
        onClick={() => setShowPresets(!showPresets)}
        disabled={disabled || presets.length === 0}
        className={clsx(
          'ml-2 inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors',
          disabled || presets.length === 0
            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
            : 'bg-purple-50 text-purple-700 hover:bg-purple-100 active:bg-purple-200'
        )}
        title={`${presets.length} saved presets`}
        aria-label="Load filter preset"
        aria-haspopup="menu"
        aria-expanded={showPresets}
      >
        <Star className="w-4 h-4" />
        <span className="text-sm">
          Presets
          {presets.length > 0 && <span className="ml-1 bg-purple-600 text-white text-xs rounded-full px-2">
            {presets.length}
          </span>}
        </span>
      </button>

      {/* Save Preset Modal */}
      {showSaveModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowSaveModal(false)}>
          <div
            className="bg-white rounded-lg shadow-lg p-6 w-96"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-label="Save filter preset"
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold text-gray-900">
                {editingId ? 'Edit Preset' : 'Save Filter Preset'}
              </h2>
              <button
                onClick={() => setShowSaveModal(false)}
                className="p-1 hover:bg-gray-100 rounded-lg transition-colors"
                aria-label="Close"
              >
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            <input
              type="text"
              value={presetName}
              onChange={e => setPresetName(e.target.value)}
              placeholder="Enter preset name (e.g., 'High Value Customers')"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500 mb-4"
              onKeyPress={e => e.key === 'Enter' && handleSavePreset()}
              autoFocus
            />

            <div className="bg-gray-50 rounded-lg p-3 mb-4">
              <p className="text-xs text-gray-600 font-medium mb-2">Active filters:</p>
              <div className="text-sm text-gray-700">
                {hasActiveFilters ? (
                  <ul className="space-y-1">
                    {Object.entries(currentFilters).map(([key, value]) => {
                      if (value === undefined || value === '' || value === null) return null;
                      return (
                        <li key={key} className="flex justify-between">
                          <span className="font-medium">{key}:</span>
                          <span>{String(value)}</span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="text-gray-500 italic">No active filters</p>
                )}
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => setShowSaveModal(false)}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleSavePreset}
                className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 transition-colors font-medium"
              >
                {editingId ? 'Update' : 'Save'} Preset
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Presets Dropdown */}
      {showPresets && (
        <div
          className="absolute right-0 mt-2 w-80 bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-96 overflow-y-auto"
          role="menu"
          aria-orientation="vertical"
        >
          {sortedPresets.length === 0 ? (
            <div className="p-4 text-center text-gray-500 text-sm">
              No saved presets yet. Save a filter preset to see it here.
            </div>
          ) : (
            <div className="divide-y">
              {sortedPresets.map(preset => (
                <div
                  key={preset.id}
                  className="flex items-center justify-between gap-2 px-4 py-3 hover:bg-gray-50 transition-colors group"
                  role="menuitem"
                >
                  <button
                    onClick={() => handleLoadPreset(preset)}
                    className="flex-1 text-left"
                  >
                    <div className="font-medium text-gray-900">{preset.name}</div>
                    <div className="text-xs text-gray-500">
                      {Object.keys(preset.filters).length} filters
                    </div>
                  </button>

                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => handleToggleFavorite(preset.id)}
                      className="p-1 hover:bg-yellow-100 rounded transition-colors"
                      title={preset.isFavorite ? 'Remove from favorites' : 'Add to favorites'}
                      aria-label={preset.isFavorite ? 'Remove from favorites' : 'Add to favorites'}
                    >
                      <Star
                        className={clsx(
                          'w-4 h-4',
                          preset.isFavorite ? 'fill-yellow-400 text-yellow-400' : 'text-gray-400'
                        )}
                      />
                    </button>
                    <button
                      onClick={() => {
                        setShowPresets(false);
                        setShowSaveModal(true);
                        setEditingId(preset.id);
                        setPresetName(preset.name);
                      }}
                      className="p-1 hover:bg-blue-100 rounded transition-colors text-blue-600"
                      title="Edit preset"
                      aria-label="Edit preset"
                    >
                      ✎
                    </button>
                    <button
                      onClick={() => handleDeletePreset(preset.id)}
                      className="p-1 hover:bg-red-100 rounded transition-colors"
                      title="Delete preset"
                      aria-label="Delete preset"
                    >
                      <Trash2 className="w-4 h-4 text-red-600" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
