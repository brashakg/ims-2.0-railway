// ============================================================================
// IMS 2.0 - Frame & Lens Management
// ============================================================================
// Manage frames, lenses, coatings, and lens configurations

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, Package } from 'lucide-react';
import clsx from 'clsx';

export interface LensCoating {
  id: string;
  name: string;
  description?: string;
  price: number;
}

export interface Frame {
  id: string;
  code: string;
  name: string;
  brand: string;
  color: string;
  material: string; // metal, plastic, titanium, etc.
  size: string; // 48-20-140 format
  shape: 'round' | 'oval' | 'rectangular' | 'cat-eye' | 'aviator' | 'oversized';
  price: number;
  quantity: number;
  minStock: number;
  vendor?: string;
  imageUrl?: string;
  createdAt: string;
}

export interface Lens {
  id: string;
  code: string;
  name: string;
  type: 'single-vision' | 'bifocal' | 'progressive' | 'blue-light-filter';
  material: 'glass' | 'polycarbonate' | 'plastic' | 'high-index';
  refractive: string; // 1.5, 1.6, 1.67, 1.74
  price: number;
  quantity: number;
  minStock: number;
  coatings: LensCoating[];
  createdAt: string;
}

interface FrameLensManagementProps {
  frames: Frame[];
  lenses: Lens[];
  onCreateFrame: (frame: Omit<Frame, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateFrame: (frame: Frame) => Promise<void>;
  onDeleteFrame: (id: string) => Promise<void>;
  onCreateLens: (lens: Omit<Lens, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateLens: (lens: Lens) => Promise<void>;
  onDeleteLens: (id: string) => Promise<void>;
  loading?: boolean;
}

type TabType = 'frames' | 'lenses';

export function FrameLensManagement({
  frames,
  lenses,
  onCreateFrame,
  onUpdateFrame,
  onDeleteFrame,
  onCreateLens,
  onUpdateLens,
  onDeleteLens,
  loading = false,
}: FrameLensManagementProps) {
  const [activeTab, setActiveTab] = useState<TabType>('frames');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<any>({});

  const filteredFrames = frames.filter(f =>
    f.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    f.brand.toLowerCase().includes(searchTerm.toLowerCase()) ||
    f.code.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredLenses = lenses.filter(l =>
    l.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    l.code.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSaveFrame = async () => {
    if (!formData.code || !formData.name || !formData.brand || !formData.price) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateFrame({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
      } as Frame));
    } else {
      await Promise.resolve(onCreateFrame({
        ...formData,
        createdAt: new Date().toISOString(),
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const handleSaveLens = async () => {
    if (!formData.code || !formData.name || !formData.type || !formData.price) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateLens({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
      } as Lens));
    } else {
      await Promise.resolve(onCreateLens({
        ...formData,
        createdAt: new Date().toISOString(),
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Package className="w-5 h-5" />
            Frame & Lens Management
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Manage frames and lenses inventory
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData(activeTab === 'frames' ? { quantity: 0, minStock: 5 } : { quantity: 0, minStock: 10, coatings: [] });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          {activeTab === 'frames' ? 'New Frame' : 'New Lens'}
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-800 flex">
        <button
          onClick={() => setActiveTab('frames')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors',
            activeTab === 'frames'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Frames ({frames.length})
        </button>
        <button
          onClick={() => setActiveTab('lenses')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors',
            activeTab === 'lenses'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Lenses ({lenses.length})
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder={activeTab === 'frames' ? 'Search frames...' : 'Search lenses...'}
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Content */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading...</p>
          </div>
        ) : activeTab === 'frames' ? (
          filteredFrames.length === 0 ? (
            <div className="p-8 text-center text-gray-500 dark:text-gray-400">
              <Package className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No frames found</p>
            </div>
          ) : (
            filteredFrames.map(frame => (
              <div key={frame.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-gray-900 dark:text-white">{frame.name}</h3>
                      <span className="text-xs bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-2 py-1 rounded">
                        {frame.code}
                      </span>
                    </div>
                    <div className="text-sm text-gray-600 dark:text-gray-400 space-y-1">
                      <p>Brand: {frame.brand} | Color: {frame.color} | Material: {frame.material}</p>
                      <p>Size: {frame.size} | Shape: {frame.shape}</p>
                      <div className="flex gap-4 text-xs">
                        <span>Price: ${frame.price.toFixed(2)}</span>
                        <span className={frame.quantity > frame.minStock ? 'text-green-600' : 'text-red-600'}>
                          Stock: {frame.quantity} {frame.quantity <= frame.minStock && '(Low)'}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        setFormData(frame);
                        setEditingId(frame.id);
                        setShowCreateModal(true);
                      }}
                      className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                      title="Edit"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => {
                        if (confirm(`Delete frame ${frame.name}?`)) {
                          onDeleteFrame(frame.id);
                        }
                      }}
                      className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))
          )
        ) : filteredLenses.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Package className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No lenses found</p>
          </div>
        ) : (
          filteredLenses.map(lens => (
            <div key={lens.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-semibold text-gray-900 dark:text-white">{lens.name}</h3>
                    <span className="text-xs bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-2 py-1 rounded">
                      {lens.code}
                    </span>
                  </div>
                  <div className="text-sm text-gray-600 dark:text-gray-400 space-y-1">
                    <p>Type: {lens.type} | Material: {lens.material} | Refractive Index: {lens.refractive}</p>
                    {lens.coatings.length > 0 && (
                      <p>Coatings: {lens.coatings.map(c => c.name).join(', ')}</p>
                    )}
                    <div className="flex gap-4 text-xs">
                      <span>Price: ${lens.price.toFixed(2)}</span>
                      <span className={lens.quantity > lens.minStock ? 'text-green-600' : 'text-red-600'}>
                        Stock: {lens.quantity} {lens.quantity <= lens.minStock && '(Low)'}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      setFormData(lens);
                      setEditingId(lens.id);
                      setShowCreateModal(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete lens ${lens.name}?`)) {
                        onDeleteLens(lens.id);
                      }
                    }}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? `Edit ${activeTab === 'frames' ? 'Frame' : 'Lens'}` : `Create New ${activeTab === 'frames' ? 'Frame' : 'Lens'}`}
            </h2>

            {activeTab === 'frames' ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <input
                    type="text"
                    placeholder="Code *"
                    value={formData.code || ''}
                    onChange={e => setFormData({ ...formData, code: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="text"
                    placeholder="Frame Name *"
                    value={formData.name || ''}
                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="text"
                    placeholder="Brand *"
                    value={formData.brand || ''}
                    onChange={e => setFormData({ ...formData, brand: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="text"
                    placeholder="Color"
                    value={formData.color || ''}
                    onChange={e => setFormData({ ...formData, color: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <select
                    value={formData.material || ''}
                    onChange={e => setFormData({ ...formData, material: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Select Material</option>
                    <option value="metal">Metal</option>
                    <option value="plastic">Plastic</option>
                    <option value="titanium">Titanium</option>
                    <option value="acetate">Acetate</option>
                  </select>
                  <input
                    type="text"
                    placeholder="Size (e.g., 48-20-140)"
                    value={formData.size || ''}
                    onChange={e => setFormData({ ...formData, size: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <select
                    value={formData.shape || ''}
                    onChange={e => setFormData({ ...formData, shape: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Select Shape</option>
                    <option value="round">Round</option>
                    <option value="oval">Oval</option>
                    <option value="rectangular">Rectangular</option>
                    <option value="cat-eye">Cat Eye</option>
                    <option value="aviator">Aviator</option>
                    <option value="oversized">Oversized</option>
                  </select>
                  <input
                    type="number"
                    step="0.01"
                    placeholder="Price *"
                    value={formData.price || ''}
                    onChange={e => setFormData({ ...formData, price: parseFloat(e.target.value) })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="number"
                    placeholder="Quantity"
                    value={formData.quantity || 0}
                    onChange={e => setFormData({ ...formData, quantity: parseInt(e.target.value) })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setShowCreateModal(false)}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSaveFrame}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                  >
                    {editingId ? 'Update' : 'Create'} Frame
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <input
                    type="text"
                    placeholder="Code *"
                    value={formData.code || ''}
                    onChange={e => setFormData({ ...formData, code: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="text"
                    placeholder="Lens Name *"
                    value={formData.name || ''}
                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <select
                    value={formData.type || ''}
                    onChange={e => setFormData({ ...formData, type: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Select Type *</option>
                    <option value="single-vision">Single Vision</option>
                    <option value="bifocal">Bifocal</option>
                    <option value="progressive">Progressive</option>
                    <option value="blue-light-filter">Blue Light Filter</option>
                  </select>
                  <select
                    value={formData.material || ''}
                    onChange={e => setFormData({ ...formData, material: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Select Material</option>
                    <option value="glass">Glass</option>
                    <option value="polycarbonate">Polycarbonate</option>
                    <option value="plastic">Plastic</option>
                    <option value="high-index">High Index</option>
                  </select>
                  <select
                    value={formData.refractive || ''}
                    onChange={e => setFormData({ ...formData, refractive: e.target.value })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="">Refractive Index</option>
                    <option value="1.5">1.5</option>
                    <option value="1.6">1.6</option>
                    <option value="1.67">1.67</option>
                    <option value="1.74">1.74</option>
                  </select>
                  <input
                    type="number"
                    step="0.01"
                    placeholder="Price *"
                    value={formData.price || ''}
                    onChange={e => setFormData({ ...formData, price: parseFloat(e.target.value) })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <input
                    type="number"
                    placeholder="Quantity"
                    value={formData.quantity || 0}
                    onChange={e => setFormData({ ...formData, quantity: parseInt(e.target.value) })}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setShowCreateModal(false)}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSaveLens}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                  >
                    {editingId ? 'Update' : 'Create'} Lens
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
