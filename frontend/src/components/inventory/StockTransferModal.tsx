// ============================================================================
// IMS 2.0 - Stock Transfer Modal
// ============================================================================
// Complete stock transfer workflow with approval and receiving

import { useState, useEffect } from 'react';
import {
  X,
  ArrowRightLeft,
  Search,
  Plus,
  Trash2,
  Send,
  Loader2,
  AlertCircle,
  Package,
  Building2,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';

interface StockTransferModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTransferCreated: () => void;
}

interface TransferItem {
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  availableQuantity: number;
}

interface StockItem {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  brand: string;
  quantity: number;
  reservedQuantity: number;
  locationCode: string;
}

interface Store {
  id: string;
  name: string;
  code: string;
}

export function StockTransferModal({ isOpen, onClose, onTransferCreated }: StockTransferModalProps) {
  const { user } = useAuth();
  const toast = useToast();

  const [step, setStep] = useState<'details' | 'items' | 'review'>('details');
  const [isSending, setIsSending] = useState(false);

  // Transfer details
  const [destinationStore, setDestinationStore] = useState('');
  const [stores, setStores] = useState<Store[]>([]);
  const [notes, setNotes] = useState('');

  // Items
  const [transferItems, setTransferItems] = useState<TransferItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<StockItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadStores();
      resetForm();
    }
  }, [isOpen]);

  const loadStores = async () => {
    try {
      // Mock stores for now - in production, fetch from API
      const mockStores: Store[] = [
        { id: 'store-1', name: 'Main Store - Mumbai', code: 'MUM01' },
        { id: 'store-2', name: 'Branch Store - Pune', code: 'PUN01' },
        { id: 'store-3', name: 'Branch Store - Delhi', code: 'DEL01' },
        { id: 'store-4', name: 'Warehouse - Bangalore', code: 'BLR01' },
      ];

      // Filter out current store
      const otherStores = mockStores.filter(s => s.id !== user?.activeStoreId);
      setStores(otherStores);
    } catch (error: any) {
      toast.error('Failed to load stores');
    }
  };

  const resetForm = () => {
    setStep('details');
    setDestinationStore('');
    setNotes('');
    setTransferItems([]);
    setSearchQuery('');
    setSearchResults([]);
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }

    setIsSearching(true);
    try {
      // Get all stock for the store and filter client-side
      // In production, the API should support search parameter
      const stock = await inventoryApi.getStock(user?.activeStoreId || '');

      // Filter by search query and availability
      const availableStock = stock.filter((item: StockItem) => {
        const matchesSearch =
          item.productName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
          item.sku?.toLowerCase().includes(searchQuery.toLowerCase());

        const notInTransfer = !transferItems.some(ti => ti.productId === item.productId);
        const hasAvailableQty = (item.quantity - item.reservedQuantity) > 0;

        return matchesSearch && notInTransfer && hasAvailableQty;
      });

      setSearchResults(availableStock);
    } catch (error: any) {
      toast.error('Failed to search products');
    } finally {
      setIsSearching(false);
    }
  };

  const handleAddItem = (stockItem: StockItem) => {
    const availableQty = stockItem.quantity - stockItem.reservedQuantity;

    const newItem: TransferItem = {
      productId: stockItem.productId,
      productName: stockItem.productName,
      sku: stockItem.sku,
      quantity: 1,
      availableQuantity: availableQty,
    };

    setTransferItems([...transferItems, newItem]);
    setSearchQuery('');
    setSearchResults([]);
    toast.success(`Added ${stockItem.productName} to transfer`);
  };

  const handleRemoveItem = (productId: string) => {
    setTransferItems(transferItems.filter(item => item.productId !== productId));
  };

  const handleQuantityChange = (productId: string, quantity: number) => {
    setTransferItems(
      transferItems.map(item =>
        item.productId === productId
          ? { ...item, quantity: Math.max(1, Math.min(quantity, item.availableQuantity)) }
          : item
      )
    );
  };

  const handleNext = () => {
    if (step === 'details') {
      if (!destinationStore) {
        toast.error('Please select a destination store');
        return;
      }
      setStep('items');
    } else if (step === 'items') {
      if (transferItems.length === 0) {
        toast.error('Please add at least one item to transfer');
        return;
      }
      setStep('review');
    }
  };

  const handleBack = () => {
    if (step === 'items') {
      setStep('details');
    } else if (step === 'review') {
      setStep('items');
    }
  };

  const handleSubmit = async () => {
    setIsSending(true);
    try {
      await inventoryApi.createTransfer({
        fromStoreId: user?.activeStoreId || '',
        toStoreId: destinationStore,
        items: transferItems.map(item => ({
          stockId: item.productId,
          quantity: item.quantity,
        })),
      });

      toast.success('Transfer request created successfully');
      onTransferCreated();
      onClose();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to create transfer');
    } finally {
      setIsSending(false);
    }
  };

  if (!isOpen) return null;

  const selectedStoreName = stores.find(s => s.id === destinationStore)?.name || '';
  const totalItems = transferItems.reduce((sum, item) => sum + item.quantity, 0);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-100 rounded-lg">
              <ArrowRightLeft className="w-6 h-6 text-purple-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Create Stock Transfer</h2>
              <p className="text-sm text-gray-500">Transfer stock between stores</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSending}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Progress Steps */}
        <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center justify-between max-w-2xl mx-auto">
            <div className="flex flex-col items-center flex-1">
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center font-medium ${
                  step === 'details'
                    ? 'bg-purple-600 text-white'
                    : 'bg-green-500 text-white'
                }`}
              >
                {step === 'details' ? '1' : '✓'}
              </div>
              <span className="text-xs mt-2 font-medium text-gray-700">Details</span>
            </div>
            <div className="flex-1 h-1 bg-gray-300 mx-2">
              <div
                className={`h-full transition-all ${
                  step !== 'details' ? 'bg-purple-600' : 'bg-gray-300'
                }`}
              />
            </div>
            <div className="flex flex-col items-center flex-1">
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center font-medium ${
                  step === 'items'
                    ? 'bg-purple-600 text-white'
                    : step === 'review'
                    ? 'bg-green-500 text-white'
                    : 'bg-gray-300 text-gray-600'
                }`}
              >
                {step === 'review' ? '✓' : '2'}
              </div>
              <span className="text-xs mt-2 font-medium text-gray-700">Items</span>
            </div>
            <div className="flex-1 h-1 bg-gray-300 mx-2">
              <div
                className={`h-full transition-all ${
                  step === 'review' ? 'bg-purple-600' : 'bg-gray-300'
                }`}
              />
            </div>
            <div className="flex flex-col items-center flex-1">
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center font-medium ${
                  step === 'review'
                    ? 'bg-purple-600 text-white'
                    : 'bg-gray-300 text-gray-600'
                }`}
              >
                3
              </div>
              <span className="text-xs mt-2 font-medium text-gray-700">Review</span>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto" style={{ maxHeight: 'calc(90vh - 280px)' }}>
          {step === 'details' && (
            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Destination Store *
                </label>
                <select
                  value={destinationStore}
                  onChange={(e) => setDestinationStore(e.target.value)}
                  className="input-field w-full"
                >
                  <option value="">Select destination store</option>
                  {stores.map((store) => (
                    <option key={store.id} value={store.id}>
                      {store.name} ({store.code})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Notes (Optional)
                </label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Add any notes or special instructions..."
                  rows={4}
                  className="input-field w-full"
                />
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div className="flex gap-3">
                  <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
                  <div className="text-sm text-blue-900">
                    <p className="font-medium mb-1">Transfer Guidelines</p>
                    <ul className="list-disc list-inside space-y-1 text-blue-800">
                      <li>Only available stock (not reserved) can be transferred</li>
                      <li>Receiving store must accept the transfer</li>
                      <li>Barcodes will need to be reprinted at destination</li>
                      <li>Transfer cannot be cancelled once sent</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          )}

          {step === 'items' && (
            <div className="space-y-6">
              {/* Search */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Search Products
                </label>
                <div className="flex gap-2">
                  <div className="flex-1 relative">
                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                      placeholder="Search by name, SKU, or barcode..."
                      className="input-field pl-10 w-full"
                    />
                  </div>
                  <button
                    onClick={handleSearch}
                    disabled={isSearching}
                    className="btn-primary flex items-center gap-2"
                  >
                    {isSearching ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Search className="w-4 h-4" />
                    )}
                    Search
                  </button>
                </div>
              </div>

              {/* Search Results */}
              {searchResults.length > 0 && (
                <div className="border border-gray-200 rounded-lg overflow-hidden">
                  <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
                    <p className="text-sm font-medium text-gray-700">
                      Search Results ({searchResults.length})
                    </p>
                  </div>
                  <div className="divide-y divide-gray-200 max-h-64 overflow-y-auto">
                    {searchResults.map((item) => (
                      <div
                        key={item.id}
                        className="p-4 flex items-center justify-between hover:bg-gray-50"
                      >
                        <div className="flex-1">
                          <p className="font-medium text-gray-900">{item.productName}</p>
                          <p className="text-sm text-gray-500">
                            SKU: {item.sku} • Brand: {item.brand}
                          </p>
                          <p className="text-sm text-gray-600 mt-1">
                            Available: {item.quantity - item.reservedQuantity} units
                            {item.locationCode && ` • Location: ${item.locationCode}`}
                          </p>
                        </div>
                        <button
                          onClick={() => handleAddItem(item)}
                          className="btn-primary text-sm flex items-center gap-2"
                        >
                          <Plus className="w-4 h-4" />
                          Add
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Transfer Items */}
              {transferItems.length > 0 ? (
                <div className="border border-gray-200 rounded-lg overflow-hidden">
                  <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
                    <p className="text-sm font-medium text-gray-700">
                      Items to Transfer ({transferItems.length})
                    </p>
                  </div>
                  <div className="divide-y divide-gray-200">
                    {transferItems.map((item) => (
                      <div key={item.productId} className="p-4 flex items-center gap-4">
                        <div className="flex-1">
                          <p className="font-medium text-gray-900">{item.productName}</p>
                          <p className="text-sm text-gray-500">SKU: {item.sku}</p>
                          <p className="text-xs text-gray-400 mt-1">
                            Available: {item.availableQuantity} units
                          </p>
                        </div>
                        <div className="flex items-center gap-4">
                          <div className="flex items-center gap-2">
                            <label className="text-sm text-gray-600">Quantity:</label>
                            <input
                              type="number"
                              min="1"
                              max={item.availableQuantity}
                              value={item.quantity}
                              onChange={(e) =>
                                handleQuantityChange(item.productId, parseInt(e.target.value) || 1)
                              }
                              className="input-field w-20 text-center"
                            />
                          </div>
                          <button
                            onClick={() => handleRemoveItem(item.productId)}
                            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-center py-12 text-gray-500">
                  <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>No items added yet</p>
                  <p className="text-sm">Search and add products to transfer</p>
                </div>
              )}
            </div>
          )}

          {step === 'review' && (
            <div className="space-y-6">
              {/* Transfer Summary */}
              <div className="bg-gradient-to-br from-purple-50 to-blue-50 border border-purple-200 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Transfer Summary</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex items-center gap-3">
                    <Building2 className="w-5 h-5 text-gray-600" />
                    <div>
                      <p className="text-sm text-gray-600">Destination</p>
                      <p className="font-medium text-gray-900">{selectedStoreName}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Package className="w-5 h-5 text-gray-600" />
                    <div>
                      <p className="text-sm text-gray-600">Total Items</p>
                      <p className="font-medium text-gray-900">
                        {transferItems.length} products • {totalItems} units
                      </p>
                    </div>
                  </div>
                </div>
                {notes && (
                  <div className="mt-4 pt-4 border-t border-purple-200">
                    <p className="text-sm text-gray-600 mb-1">Notes:</p>
                    <p className="text-sm text-gray-900">{notes}</p>
                  </div>
                )}
              </div>

              {/* Items List */}
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Product</th>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">SKU</th>
                      <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Quantity</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {transferItems.map((item) => (
                      <tr key={item.productId}>
                        <td className="px-4 py-3 text-sm text-gray-900">{item.productName}</td>
                        <td className="px-4 py-3 text-sm text-gray-500">{item.sku}</td>
                        <td className="px-4 py-3 text-sm text-gray-900 text-right">
                          {item.quantity}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
          <button
            onClick={step === 'details' ? onClose : handleBack}
            disabled={isSending}
            className="btn-outline"
          >
            {step === 'details' ? 'Cancel' : 'Back'}
          </button>
          <div className="flex items-center gap-3">
            {step !== 'review' && (
              <button onClick={handleNext} className="btn-primary">
                Next
              </button>
            )}
            {step === 'review' && (
              <button
                onClick={handleSubmit}
                disabled={isSending}
                className="btn-primary flex items-center gap-2"
              >
                {isSending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Sending...
                  </>
                ) : (
                  <>
                    <Send className="w-4 h-4" />
                    Send Transfer
                  </>
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
