'use client';

import { useState, useEffect } from 'react';
import { Loader2, Plus, Search, X } from 'lucide-react';
import Topbar from '@/components/Topbar';

interface Transfer {
  id: string;
  transferNumber: string;
  fromLocationId: string;
  fromLocationName: string;
  toLocationId: string;
  toLocationName: string;
  status: 'PENDING' | 'IN_TRANSIT' | 'COMPLETED' | 'CANCELLED';
  itemsCount: number;
  createdAt: string;
  items: Array<{
    id: string;
    productId: string;
    productName: string;
    quantity: number;
  }>;
}

interface Location {
  id: string;
  name: string;
  code: string;
}

interface Product {
  id: string;
  title: string;
}

interface TransferItem {
  productId: string;
  productName: string;
  quantity: number;
}

export default function StockTransfersPage() {
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [locations, setLocations] = useState<Location[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Modal form state
  const [fromLocation, setFromLocation] = useState('');
  const [toLocation, setToLocation] = useState('');
  const [productSearch, setProductSearch] = useState('');
  const [productSearchResults, setProductSearchResults] = useState<Product[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [items, setItems] = useState<TransferItem[]>([]);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [selectedProductName, setSelectedProductName] = useState('');
  const [quantity, setQuantity] = useState('');

  const ITEMS_PER_PAGE = 10;

  // Fetch transfers
  useEffect(() => {
    const fetchTransfers = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          page: page.toString(),
          limit: ITEMS_PER_PAGE.toString(),
        });
        const res = await fetch(`/api/stock-transfers?${params}`);
        if (!res.ok) throw new Error('Failed to fetch transfers');
        const data = await res.json();
        setTransfers(data.transfers || []);
        setTotalPages(Math.ceil(data.total / ITEMS_PER_PAGE));
      } catch (err) {
        console.error('Error fetching transfers:', err);
        setError('Failed to load transfers');
      } finally {
        setLoading(false);
      }
    };
    fetchTransfers();
  }, [page]);

  // Fetch locations
  useEffect(() => {
    const fetchLocations = async () => {
      try {
        const res = await fetch('/api/locations?excludeSynthetic=true');
        if (!res.ok) throw new Error('Failed to fetch locations');
        const data = await res.json();
        setLocations(data || []);
      } catch (err) {
        console.error('Error fetching locations:', err);
      }
    };
    fetchLocations();
  }, []);

  // Search products
  useEffect(() => {
    const searchProducts = async () => {
      if (!productSearch.trim()) {
        setProductSearchResults([]);
        return;
      }
      setSearchLoading(true);
      try {
        const res = await fetch(`/api/products?search=${encodeURIComponent(productSearch)}`);
        if (!res.ok) throw new Error('Failed to search products');
        const data = await res.json();
        setProductSearchResults(data.products || []);
      } catch (err) {
        console.error('Error searching products:', err);
        setProductSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    };

    const timer = setTimeout(searchProducts, 300);
    return () => clearTimeout(timer);
  }, [productSearch]);

  const handleAddItem = () => {
    if (!selectedProductId || !quantity || parseInt(quantity) <= 0) {
      alert('Please select a product and enter a valid quantity');
      return;
    }

    const newItem: TransferItem = {
      productId: selectedProductId,
      productName: selectedProductName,
      quantity: parseInt(quantity),
    };

    setItems([...items, newItem]);
    setSelectedProductId('');
    setSelectedProductName('');
    setProductSearch('');
    setQuantity('');
    setProductSearchResults([]);
  };

  const handleRemoveItem = (index: number) => {
    setItems(items.filter((_, i) => i !== index));
  };

  const handleSubmitTransfer = async () => {
    if (!fromLocation || !toLocation || items.length === 0) {
      alert('Please fill in all required fields');
      return;
    }

    if (fromLocation === toLocation) {
      alert('From and To locations must be different');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch('/api/stock-transfers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fromLocationId: fromLocation,
          toLocationId: toLocation,
          items: items.map((item) => ({
            productId: item.productId,
            quantity: item.quantity,
          })),
        }),
      });

      if (!res.ok) throw new Error('Failed to create transfer');

      // Reset form and close modal
      setShowModal(false);
      setFromLocation('');
      setToLocation('');
      setItems([]);
      setSelectedProductId('');
      setSelectedProductName('');
      setProductSearch('');
      setQuantity('');

      // Refresh transfers list
      setPage(1);
      const params = new URLSearchParams({
        page: '1',
        limit: ITEMS_PER_PAGE.toString(),
      });
      const transfersRes = await fetch(`/api/stock-transfers?${params}`);
      const transfersData = await transfersRes.json();
      setTransfers(transfersData.transfers || []);
    } catch (err) {
      console.error('Error creating transfer:', err);
      setError('Failed to create transfer');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCompleteTransfer = async (transferId: string) => {
    try {
      const res = await fetch(`/api/stock-transfers/${transferId}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      if (!res.ok) throw new Error('Failed to complete transfer');

      // Refresh transfers
      const params = new URLSearchParams({
        page: page.toString(),
        limit: ITEMS_PER_PAGE.toString(),
      });
      const transfersRes = await fetch(`/api/stock-transfers?${params}`);
      const transfersData = await transfersRes.json();
      setTransfers(transfersData.transfers || []);
    } catch (err) {
      console.error('Error completing transfer:', err);
      setError('Failed to complete transfer');
    }
  };

  const getStatusBadge = (status: string) => {
    const styles: Record<string, string> = {
      PENDING: 'bg-yellow-100 text-yellow-800',
      IN_TRANSIT: 'bg-blue-100 text-blue-800',
      COMPLETED: 'bg-green-100 text-green-800',
      CANCELLED: 'bg-red-100 text-red-800',
    };
    return styles[status] || 'bg-gray-100 text-gray-800';
  };

  return (
    <>
      <Topbar
        title="Stock Transfers"
        subtitle="Inventory moves between locations"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Stock Transfers' }]}
        primaryAction={
          <button
            type="button"
            onClick={() => setShowModal(true)}
            className="polaris-btn polaris-btn-primary"
          >
            <Plus className="w-3.5 h-3.5" />
            New Transfer
          </button>
        }
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        {/* Error Message */}
        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-800">{error}</p>
          </div>
        )}

        {/* Transfers Table */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
            </div>
          ) : transfers.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-gray-600">No stock transfers found</p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Transfer #</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">From</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">To</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Items</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Date</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {transfers.map((transfer) => (
                      <tr key={transfer.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 text-sm font-medium text-blue-600">#{transfer.transferNumber}</td>
                        <td className="px-6 py-4 text-sm text-gray-900">{transfer.fromLocationName}</td>
                        <td className="px-6 py-4 text-sm text-gray-900">{transfer.toLocationName}</td>
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{transfer.itemsCount}</td>
                        <td className="px-6 py-4">
                          <span className={`inline-block px-3 py-1 text-xs font-medium rounded-full ${getStatusBadge(transfer.status)}`}>
                            {transfer.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-600">{new Date(transfer.createdAt).toLocaleDateString()}</td>
                        <td className="px-6 py-4">
                          {(transfer.status === 'PENDING' || transfer.status === 'IN_TRANSIT') && (
                            <button
                              onClick={() => handleCompleteTransfer(transfer.id)}
                              className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                            >
                              Complete
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200">
                <p className="text-sm text-gray-600">
                  Page {page} of {totalPages}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* New Transfer Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg shadow-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <h2 className="text-2xl font-bold text-gray-900">New Stock Transfer</h2>
              <button
                onClick={() => setShowModal(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="w-6 h-6" />
              </button>
            </div>

            <div className="p-6 space-y-6">
              {/* Location Selection */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">From Location</label>
                  <select
                    value={fromLocation}
                    onChange={(e) => setFromLocation(e.target.value)}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  >
                    <option value="">Select location</option>
                    {locations.map((loc) => (
                      <option key={loc.id} value={loc.id}>
                        {loc.name} ({loc.code})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">To Location</label>
                  <select
                    value={toLocation}
                    onChange={(e) => setToLocation(e.target.value)}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  >
                    <option value="">Select location</option>
                    {locations.map((loc) => (
                      <option key={loc.id} value={loc.id}>
                        {loc.name} ({loc.code})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Product Search */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Search Products</label>
                <div className="relative">
                  <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Type product name..."
                    value={productSearch}
                    onChange={(e) => setProductSearch(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                  {searchLoading && <Loader2 className="absolute right-3 top-2.5 w-4 h-4 animate-spin text-gray-400" />}
                </div>

                {productSearchResults.length > 0 && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-300 rounded-lg shadow-lg z-10">
                    {productSearchResults.slice(0, 5).map((product) => (
                      <button
                        key={product.id}
                        onClick={() => {
                          setSelectedProductId(product.id);
                          setSelectedProductName(product.title);
                          setProductSearch('');
                          setProductSearchResults([]);
                        }}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50 border-b last:border-b-0"
                      >
                        {product.title}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Quantity Input */}
              {selectedProductId && (
                <div className="grid grid-cols-3 gap-4">
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-2">Selected Product</label>
                    <input
                      type="text"
                      disabled
                      value={selectedProductName}
                      className="w-full px-4 py-2 bg-gray-50 border border-gray-300 rounded-lg text-gray-600"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">Quantity</label>
                    <input
                      type="number"
                      min="1"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      placeholder="0"
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                </div>
              )}

              {selectedProductId && (
                <button
                  onClick={handleAddItem}
                  className="w-full px-4 py-2 bg-blue-100 text-blue-600 rounded-lg hover:bg-blue-50 font-medium"
                >
                  Add Item
                </button>
              )}

              {/* Items List */}
              {items.length > 0 && (
                <div className="bg-gray-50 rounded-lg p-4">
                  <h3 className="font-semibold text-gray-900 mb-3">Items ({items.length})</h3>
                  <div className="space-y-2">
                    {items.map((item, index) => (
                      <div key={index} className="flex items-center justify-between bg-white p-3 rounded border border-gray-200">
                        <div>
                          <p className="text-sm font-medium text-gray-900">{item.productName}</p>
                          <p className="text-xs text-gray-600">Qty: {item.quantity}</p>
                        </div>
                        <button
                          onClick={() => handleRemoveItem(index)}
                          className="text-red-600 hover:text-red-800"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200">
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitTransfer}
                disabled={submitting || !fromLocation || !toLocation || items.length === 0}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-blue-400 font-medium"
              >
                {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                Create Transfer
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
