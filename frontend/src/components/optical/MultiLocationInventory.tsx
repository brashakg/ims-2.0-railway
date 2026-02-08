// ============================================================================
// IMS 2.0 - Multi-Location Inventory Management
// ============================================================================
// Manage inventory across multiple store locations with transfers and allocations

import { useState } from 'react';
import { Send, Package, ArrowRight, MapPin } from 'lucide-react';
import clsx from 'clsx';

export interface LocationStock {
  locationId: string;
  locationName: string;
  quantity: number;
  minStock: number;
  maxStock: number;
  lastRestocked: string;
}

export interface InventoryItem {
  id: string;
  code: string;
  name: string;
  category: string;
  totalQuantity: number;
  locations: LocationStock[];
}

export interface StockTransfer {
  id: string;
  itemId: string;
  itemName: string;
  fromLocation: string;
  toLocation: string;
  quantity: number;
  status: 'pending' | 'in-transit' | 'received' | 'cancelled';
  createdAt: string;
  receivedAt?: string;
}

interface MultiLocationInventoryProps {
  items: InventoryItem[];
  locations: { id: string; name: string }[];
  transfers: StockTransfer[];
  onTransferStock: (transfer: Omit<StockTransfer, 'id' | 'createdAt'>) => Promise<void>;
  onReceiveTransfer: (transferId: string) => Promise<void>;
  onCancelTransfer: (transferId: string) => Promise<void>;
  loading?: boolean;
}

type ViewMode = 'inventory' | 'transfers';

export function MultiLocationInventory({
  items,
  locations,
  transfers,
  onTransferStock,
  onReceiveTransfer,
  onCancelTransfer,
  loading = false,
}: MultiLocationInventoryProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('inventory');
  const [searchTerm, setSearchTerm] = useState('');
  const [showTransferModal, setShowTransferModal] = useState(false);
  const [selectedItem, setSelectedItem] = useState<InventoryItem | null>(null);
  const [fromLocation, setFromLocation] = useState('');
  const [toLocation, setToLocation] = useState('');
  const [transferQuantity, setTransferQuantity] = useState('');

  const filteredItems = items.filter(item =>
    item.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    item.code.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const pendingTransfers = transfers.filter(t => t.status === 'pending' || t.status === 'in-transit');

  const handleInitiateTransfer = (item: InventoryItem) => {
    setSelectedItem(item);
    setFromLocation('');
    setToLocation('');
    setTransferQuantity('');
    setShowTransferModal(true);
  };

  const handleConfirmTransfer = async () => {
    if (!selectedItem || !fromLocation || !toLocation || !transferQuantity) {
      alert('Please fill in all required fields');
      return;
    }

    if (fromLocation === toLocation) {
      alert('From and to locations must be different');
      return;
    }

    const fromStock = selectedItem.locations.find(l => l.locationId === fromLocation);
    if (!fromStock || fromStock.quantity < parseInt(transferQuantity)) {
      alert('Insufficient stock at source location');
      return;
    }

    await Promise.resolve(onTransferStock({
      itemId: selectedItem.id,
      itemName: selectedItem.name,
      fromLocation: fromLocation,
      toLocation: toLocation,
      quantity: parseInt(transferQuantity),
      status: 'pending',
    }));

    setShowTransferModal(false);
    setSelectedItem(null);
  };

  const getStockStatus = (quantity: number, minStock: number, maxStock: number) => {
    if (quantity <= minStock) return 'low';
    if (quantity >= maxStock) return 'high';
    return 'normal';
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'bg-yellow-100 text-yellow-700';
      case 'in-transit':
        return 'bg-blue-100 text-blue-700';
      case 'received':
        return 'bg-green-100 text-green-700';
      case 'cancelled':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStockStatusColor = (status: string) => {
    switch (status) {
      case 'low':
        return 'text-red-600 bg-red-50 dark:bg-red-900/20';
      case 'high':
        return 'text-orange-600 bg-orange-50 dark:bg-orange-900/20';
      default:
        return 'text-green-600 bg-green-50 dark:bg-green-900/20';
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <MapPin className="w-5 h-5" />
            Multi-Location Inventory
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Manage inventory across {locations.length} locations
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-800 flex">
        <button
          onClick={() => setViewMode('inventory')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors',
            viewMode === 'inventory'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Inventory by Item
        </button>
        <button
          onClick={() => setViewMode('transfers')}
          className={clsx(
            'px-6 py-3 font-medium border-b-2 transition-colors flex items-center gap-2',
            viewMode === 'transfers'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
          )}
        >
          Transfers {pendingTransfers.length > 0 && `(${pendingTransfers.length})`}
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Package className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by item name or code..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Content */}
      <div>
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading inventory...</p>
          </div>
        ) : viewMode === 'inventory' ? (
          <div className="divide-y divide-gray-200 dark:divide-gray-800">
            {filteredItems.length === 0 ? (
              <div className="p-8 text-center text-gray-500 dark:text-gray-400">
                <Package className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>No items found</p>
              </div>
            ) : (
              filteredItems.map(item => (
                <div key={item.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                  <div className="flex items-start justify-between gap-4 mb-3">
                    <div className="flex-1">
                      <h3 className="font-semibold text-gray-900 dark:text-white">{item.name}</h3>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{item.code} • {item.category}</p>
                      <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                        Total Stock: <span className="font-semibold">{item.totalQuantity}</span>
                      </p>
                    </div>
                    <button
                      onClick={() => handleInitiateTransfer(item)}
                      className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center gap-2 text-sm font-medium"
                    >
                      <Send className="w-4 h-4" />
                      Transfer
                    </button>
                  </div>

                  {/* Location Breakdown */}
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                    {item.locations.map(loc => {
                      const status = getStockStatus(loc.quantity, loc.minStock, loc.maxStock);
                      return (
                        <div
                          key={loc.locationId}
                          className={clsx(
                            'p-3 rounded-lg border',
                            getStockStatusColor(status),
                            'border-current'
                          )}
                        >
                          <p className="text-sm font-medium">{loc.locationName}</p>
                          <p className="text-lg font-bold mt-1">{loc.quantity}</p>
                          <p className="text-xs opacity-75 mt-1">
                            Min: {loc.minStock} | Max: {loc.maxStock}
                          </p>
                          {status === 'low' && (
                            <p className="text-xs font-semibold mt-2">⚠️ Low Stock</p>
                          )}
                          {status === 'high' && (
                            <p className="text-xs font-semibold mt-2">ℹ️ Overstock</p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-800">
            {transfers.length === 0 ? (
              <div className="p-8 text-center text-gray-500 dark:text-gray-400">
                <ArrowRight className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>No transfers yet</p>
              </div>
            ) : (
              transfers.map(transfer => (
                <div key={transfer.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h3 className="font-semibold text-gray-900 dark:text-white">{transfer.itemName}</h3>
                        <span className={clsx('px-2 py-1 rounded text-xs font-medium', getStatusColor(transfer.status))}>
                          {transfer.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                        <span>{transfer.fromLocation}</span>
                        <ArrowRight className="w-4 h-4" />
                        <span>{transfer.toLocation}</span>
                        <span className="ml-2 font-semibold">× {transfer.quantity}</span>
                      </div>
                      <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
                        {new Date(transfer.createdAt).toLocaleString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {transfer.status === 'pending' && (
                        <>
                          <button
                            onClick={() => onReceiveTransfer(transfer.id)}
                            className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700"
                          >
                            Receive
                          </button>
                          <button
                            onClick={() => onCancelTransfer(transfer.id)}
                            className="px-3 py-1 bg-red-600 text-white rounded text-sm hover:bg-red-700"
                          >
                            Cancel
                          </button>
                        </>
                      )}
                      {transfer.status === 'received' && (
                        <span className="text-xs text-green-600 font-semibold">
                          Received {transfer.receivedAt && new Date(transfer.receivedAt).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Transfer Modal */}
      {showTransferModal && selectedItem && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowTransferModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              Transfer Stock
            </h2>

            <div className="space-y-4">
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Item: <span className="font-semibold">{selectedItem.name}</span>
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  From Location *
                </label>
                <select
                  value={fromLocation}
                  onChange={e => setFromLocation(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="">Select location</option>
                  {selectedItem.locations.map(loc => (
                    <option key={loc.locationId} value={loc.locationId}>
                      {loc.locationName} (Available: {loc.quantity})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  To Location *
                </label>
                <select
                  value={toLocation}
                  onChange={e => setToLocation(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="">Select location</option>
                  {locations
                    .filter(loc => loc.id !== fromLocation)
                    .map(loc => (
                      <option key={loc.id} value={loc.id}>
                        {loc.name}
                      </option>
                    ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Quantity *
                </label>
                <input
                  type="number"
                  min="1"
                  value={transferQuantity}
                  onChange={e => setTransferQuantity(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
              </div>
            </div>

            <div className="flex gap-2 mt-6">
              <button
                onClick={() => setShowTransferModal(false)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmTransfer}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center justify-center gap-2 font-medium"
              >
                <Send className="w-4 h-4" />
                Initiate Transfer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
