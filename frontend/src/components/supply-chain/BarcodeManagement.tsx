// ============================================================================
// IMS 2.0 - Barcode Management
// ============================================================================
// Generate, manage, and track barcodes for products

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, Copy, Download, QrCode } from 'lucide-react';
import clsx from 'clsx';

export interface Barcode {
  id: string;
  code: string;
  format: 'CODE128' | 'EAN13' | 'UPC' | 'QR';
  productId: string;
  productName: string;
  productSKU: string;
  quantity: number;
  price: number;
  active: boolean;
  createdAt: string;
  lastScanned?: string;
  scanCount: number;
}

interface BarcodeManagementProps {
  barcodes: Barcode[];
  onCreateBarcode: (barcode: Omit<Barcode, 'id' | 'createdAt' | 'scanCount' | 'lastScanned'>) => Promise<void>;
  onUpdateBarcode: (barcode: Barcode) => Promise<void>;
  onDeleteBarcode: (id: string) => Promise<void>;
  onDownloadBarcodes: (ids: string[], format: string) => Promise<void>;
  loading?: boolean;
}

export function BarcodeManagement({
  barcodes,
  onCreateBarcode,
  onUpdateBarcode,
  onDeleteBarcode,
  onDownloadBarcodes,
  loading = false,
}: BarcodeManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<Barcode>>({});
  const [selectedBarcodes, setSelectedBarcodes] = useState<Set<string>>(new Set());
  const [copySuccess, setCopySuccess] = useState<string | null>(null);

  const filteredBarcodes = barcodes.filter(b =>
    b.code.toLowerCase().includes(searchTerm.toLowerCase()) ||
    b.productName.toLowerCase().includes(searchTerm.toLowerCase()) ||
    b.productSKU.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSave = async () => {
    if (!formData.code || !formData.productId || !formData.format) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateBarcode({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
        scanCount: formData.scanCount || 0,
      } as Barcode));
    } else {
      await Promise.resolve(onCreateBarcode({
        ...formData,
        createdAt: new Date().toISOString(),
        active: true,
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const handleCopyCode = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopySuccess(code);
    setTimeout(() => setCopySuccess(null), 2000);
  };

  const handleSelectBarcode = (id: string) => {
    const newSelected = new Set(selectedBarcodes);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedBarcodes(newSelected);
  };

  const handleSelectAll = () => {
    if (selectedBarcodes.size === filteredBarcodes.length) {
      setSelectedBarcodes(new Set());
    } else {
      setSelectedBarcodes(new Set(filteredBarcodes.map(b => b.id)));
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <QrCode className="w-5 h-5" />
            Barcode Management
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredBarcodes.length} of {barcodes.length} barcodes
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ format: 'CODE128', active: true });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Barcode
        </button>
      </div>

      {/* Search and Bulk Actions */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 space-y-3">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by barcode, product name, or SKU..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>

        {selectedBarcodes.size > 0 && (
          <div className="flex items-center gap-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {selectedBarcodes.size} selected
            </span>
            <button
              onClick={() => onDownloadBarcodes(Array.from(selectedBarcodes), 'pdf')}
              className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium flex items-center gap-1"
            >
              <Download className="w-4 h-4" />
              Download
            </button>
            <button
              onClick={() => setSelectedBarcodes(new Set())}
              className="px-3 py-1 bg-gray-300 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded text-sm hover:bg-gray-400 dark:hover:bg-gray-600"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      {/* Barcodes List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading barcodes...</p>
          </div>
        ) : filteredBarcodes.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <QrCode className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No barcodes found</p>
          </div>
        ) : (
          <>
            {/* Header Row */}
            <div className="p-4 bg-gray-50 dark:bg-gray-800 text-sm font-medium text-gray-700 dark:text-gray-300 grid grid-cols-8 gap-2 items-center">
              <input
                type="checkbox"
                checked={selectedBarcodes.size === filteredBarcodes.length && filteredBarcodes.length > 0}
                onChange={handleSelectAll}
                className="w-4 h-4 rounded text-blue-600"
              />
              <div>Code</div>
              <div>Format</div>
              <div>Product</div>
              <div>SKU</div>
              <div className="text-right">Scans</div>
              <div className="text-right">Price</div>
              <div className="text-right">Actions</div>
            </div>

            {/* Barcode Rows */}
            {filteredBarcodes.map(barcode => (
              <div key={barcode.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors grid grid-cols-8 gap-2 items-center">
                <input
                  type="checkbox"
                  checked={selectedBarcodes.has(barcode.id)}
                  onChange={() => handleSelectBarcode(barcode.id)}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <div className="text-sm font-mono text-gray-900 dark:text-white truncate">
                  {barcode.code}
                </div>
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  <span className="px-2 py-1 bg-gray-200 dark:bg-gray-700 rounded text-xs font-medium">
                    {barcode.format}
                  </span>
                </div>
                <div className="text-sm text-gray-900 dark:text-white truncate">
                  {barcode.productName}
                </div>
                <div className="text-sm text-gray-600 dark:text-gray-400 font-mono">
                  {barcode.productSKU}
                </div>
                <div className="text-sm text-right text-gray-600 dark:text-gray-400">
                  <span className={clsx(
                    'px-2 py-1 rounded text-xs font-medium',
                    barcode.scanCount > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'
                  )}>
                    {barcode.scanCount}
                  </span>
                </div>
                <div className="text-sm text-right font-semibold text-gray-900 dark:text-white">
                  ${barcode.price.toFixed(2)}
                </div>
                <div className="flex items-center gap-1 justify-end">
                  <button
                    onClick={() => handleCopyCode(barcode.code)}
                    className="p-1 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded text-blue-600 dark:text-blue-400"
                    title={copySuccess === barcode.code ? 'Copied!' : 'Copy code'}
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      setFormData(barcode);
                      setEditingId(barcode.id);
                      setShowCreateModal(true);
                    }}
                    className="p-1 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete barcode ${barcode.code}?`)) {
                        onDeleteBarcode(barcode.id);
                      }
                    }}
                    className="p-1 hover:bg-red-100 dark:hover:bg-red-900/20 rounded text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Barcode' : 'Create New Barcode'}
            </h2>

            <div className="space-y-4">
              <input
                type="text"
                placeholder="Barcode Code *"
                value={formData.code || ''}
                onChange={e => setFormData({ ...formData, code: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />

              <select
                value={formData.format || 'CODE128'}
                onChange={e => setFormData({ ...formData, format: e.target.value as any })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="CODE128">CODE128</option>
                <option value="EAN13">EAN13</option>
                <option value="UPC">UPC</option>
                <option value="QR">QR Code</option>
              </select>

              <input
                type="text"
                placeholder="Product ID *"
                value={formData.productId || ''}
                onChange={e => setFormData({ ...formData, productId: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />

              <input
                type="text"
                placeholder="Product Name"
                value={formData.productName || ''}
                onChange={e => setFormData({ ...formData, productName: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />

              <input
                type="text"
                placeholder="Product SKU"
                value={formData.productSKU || ''}
                onChange={e => setFormData({ ...formData, productSKU: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />

              <div className="grid grid-cols-2 gap-2">
                <input
                  type="number"
                  placeholder="Quantity"
                  value={formData.quantity || 0}
                  onChange={e => setFormData({ ...formData, quantity: parseInt(e.target.value) })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="number"
                  step="0.01"
                  placeholder="Price"
                  value={formData.price || 0}
                  onChange={e => setFormData({ ...formData, price: parseFloat(e.target.value) })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.active !== false}
                  onChange={e => setFormData({ ...formData, active: e.target.checked })}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">Active</span>
              </label>

              <div className="flex gap-2">
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                >
                  {editingId ? 'Update' : 'Create'} Barcode
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
