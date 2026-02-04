// ============================================================================
// IMS 2.0 - Stock Acceptance (GRN) Component
// ============================================================================
// Handles stock acceptance flow: Verify → Assign Location → Print Barcode → Accept

import { useState } from 'react';
import {
  Package,
  CheckCircle,
  AlertTriangle,
  Printer,
  MapPin,
  ChevronRight,
} from 'lucide-react';
import clsx from 'clsx';

interface PendingStock {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  brand: string;
  category: string;
  expectedQty: number;
  receivedQty: number | null;
  source: 'HQ' | 'TRANSFER' | 'VENDOR';
  sourceRef: string;
  location: string | null;
  status: 'PENDING' | 'COUNTED' | 'LOCATED' | 'ACCEPTED';
  mismatch: boolean;
}

// Mock pending stock
const mockPendingStock: PendingStock[] = [
  {
    id: 'grn-001',
    productId: 'prod-001',
    productName: 'Ray-Ban RB5154 Clubmaster',
    sku: 'RB-5154-BLK',
    brand: 'Ray-Ban',
    category: 'FRAME',
    expectedQty: 5,
    receivedQty: null,
    source: 'HQ',
    sourceRef: 'HQ-TRANS-001',
    location: null,
    status: 'PENDING',
    mismatch: false,
  },
  {
    id: 'grn-002',
    productId: 'prod-002',
    productName: 'Essilor Crizal Prevencia',
    sku: 'ESS-CP-STD',
    brand: 'Essilor',
    category: 'OPTICAL_LENS',
    expectedQty: 20,
    receivedQty: 18,
    source: 'VENDOR',
    sourceRef: 'PO-2501-001',
    location: null,
    status: 'COUNTED',
    mismatch: true,
  },
  {
    id: 'grn-003',
    productId: 'prod-003',
    productName: 'Acuvue Oasys (6 pack)',
    sku: 'ACV-OAS-6',
    brand: 'Acuvue',
    category: 'CONTACT_LENS',
    expectedQty: 30,
    receivedQty: 30,
    source: 'HQ',
    sourceRef: 'HQ-TRANS-002',
    location: 'C1-01',
    status: 'LOCATED',
    mismatch: false,
  },
];

// Location options
const LOCATIONS = [
  'A1-01', 'A1-02', 'A1-03', 'A1-04', 'A1-05',
  'B1-01', 'B1-02', 'B1-03', 'B1-04', 'B1-05',
  'C1-01', 'C1-02', 'C1-03', 'C1-04', 'C1-05',
  'D1-01', 'D1-02', 'D1-03', 'D1-04', 'D1-05',
];

export function StockAcceptance() {
  const [pendingItems, setPendingItems] = useState<PendingStock[]>(mockPendingStock);
  const [selectedItem, setSelectedItem] = useState<PendingStock | null>(null);
  const [countInput, setCountInput] = useState('');
  const [locationInput, setLocationInput] = useState('');

  const handleVerifyCount = (item: PendingStock) => {
    const received = parseInt(countInput, 10);
    if (isNaN(received) || received < 0) return;

    setPendingItems(prev =>
      prev.map(i =>
        i.id === item.id
          ? {
              ...i,
              receivedQty: received,
              status: 'COUNTED',
              mismatch: received !== i.expectedQty,
            }
          : i
      )
    );
    setCountInput('');
    setSelectedItem(null);
  };

  const handleAssignLocation = (item: PendingStock) => {
    if (!locationInput) return;

    setPendingItems(prev =>
      prev.map(i =>
        i.id === item.id
          ? { ...i, location: locationInput, status: 'LOCATED' }
          : i
      )
    );
    setLocationInput('');
    setSelectedItem(null);
  };

  const handlePrintBarcode = (item: PendingStock) => {
    // In production, this would trigger barcode printing
    console.log('Printing barcode for:', item.sku);
    alert(`Barcode printed for ${item.productName}`);
  };

  const handleAccept = (item: PendingStock) => {
    setPendingItems(prev =>
      prev.map(i =>
        i.id === item.id
          ? { ...i, status: 'ACCEPTED' }
          : i
      )
    );
  };

  const getStepNumber = (status: PendingStock['status']) => {
    switch (status) {
      case 'PENDING': return 1;
      case 'COUNTED': return 2;
      case 'LOCATED': return 3;
      case 'ACCEPTED': return 4;
      default: return 0;
    }
  };

  const pendingCount = pendingItems.filter(i => i.status !== 'ACCEPTED').length;
  const mismatchCount = pendingItems.filter(i => i.mismatch).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Stock Acceptance (GRN)</h2>
          <p className="text-sm text-gray-500">Verify and accept incoming stock</p>
        </div>
        <div className="flex gap-4">
          <div className="text-center">
            <p className="text-2xl font-bold text-yellow-600">{pendingCount}</p>
            <p className="text-xs text-gray-500">Pending</p>
          </div>
          {mismatchCount > 0 && (
            <div className="text-center">
              <p className="text-2xl font-bold text-red-600">{mismatchCount}</p>
              <p className="text-xs text-gray-500">Mismatches</p>
            </div>
          )}
        </div>
      </div>

      {/* Flow Guide */}
      <div className="bg-gray-50 rounded-lg p-4">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-bv-red-600 text-white rounded-full flex items-center justify-center text-xs">1</div>
            <span>Verify Count</span>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-400" />
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-bv-red-600 text-white rounded-full flex items-center justify-center text-xs">2</div>
            <span>Assign Location</span>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-400" />
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-bv-red-600 text-white rounded-full flex items-center justify-center text-xs">3</div>
            <span>Print Barcode</span>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-400" />
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-bv-red-600 text-white rounded-full flex items-center justify-center text-xs">4</div>
            <span>Accept</span>
          </div>
        </div>
      </div>

      {/* Pending Items */}
      <div className="space-y-3">
        {pendingItems.map(item => {
          const step = getStepNumber(item.status);
          const isSelected = selectedItem?.id === item.id;

          return (
            <div
              key={item.id}
              className={clsx(
                'border rounded-lg overflow-hidden',
                item.status === 'ACCEPTED' && 'border-green-200 bg-green-50',
                item.mismatch && item.status !== 'ACCEPTED' && 'border-red-300 bg-red-50',
                !item.mismatch && item.status !== 'ACCEPTED' && 'border-gray-200 bg-white'
              )}
            >
              {/* Item Header */}
              <div className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-gray-900">{item.productName}</span>
                      {item.mismatch && (
                        <span className="badge-error flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" />
                          Mismatch
                        </span>
                      )}
                      {item.status === 'ACCEPTED' && (
                        <span className="badge-success flex items-center gap-1">
                          <CheckCircle className="w-3 h-3" />
                          Accepted
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-500">
                      {item.sku} • {item.brand} • {item.category}
                    </p>
                    <p className="text-xs text-gray-400 mt-1">
                      Source: {item.source} ({item.sourceRef})
                    </p>
                  </div>

                  {/* Quantities */}
                  <div className="text-right">
                    <div className="text-sm">
                      <span className="text-gray-500">Expected: </span>
                      <span className="font-medium">{item.expectedQty}</span>
                    </div>
                    {item.receivedQty !== null && (
                      <div className="text-sm">
                        <span className="text-gray-500">Received: </span>
                        <span className={clsx(
                          'font-medium',
                          item.mismatch ? 'text-red-600' : 'text-green-600'
                        )}>
                          {item.receivedQty}
                        </span>
                      </div>
                    )}
                    {item.location && (
                      <div className="text-sm">
                        <span className="text-gray-500">Location: </span>
                        <span className="font-medium">{item.location}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Progress */}
                <div className="mt-3 flex items-center gap-1">
                  {[1, 2, 3, 4].map(s => (
                    <div
                      key={s}
                      className={clsx(
                        'h-1 flex-1 rounded-full',
                        s <= step ? 'bg-bv-red-600' : 'bg-gray-200'
                      )}
                    />
                  ))}
                </div>
              </div>

              {/* Action Area */}
              {item.status !== 'ACCEPTED' && (
                <div className="px-4 pb-4">
                  {/* Step 1: Verify Count */}
                  {item.status === 'PENDING' && (
                    <div className="flex items-center gap-3">
                      <input
                        type="number"
                        value={isSelected ? countInput : ''}
                        onChange={e => {
                          setSelectedItem(item);
                          setCountInput(e.target.value);
                        }}
                        className="input-field w-24"
                        placeholder="Count"
                        min="0"
                      />
                      <button
                        onClick={() => handleVerifyCount(item)}
                        disabled={!countInput}
                        className="btn-primary text-sm"
                      >
                        Verify Count
                      </button>
                    </div>
                  )}

                  {/* Step 2: Assign Location */}
                  {item.status === 'COUNTED' && (
                    <div className="flex items-center gap-3">
                      <select
                        value={isSelected ? locationInput : ''}
                        onChange={e => {
                          setSelectedItem(item);
                          setLocationInput(e.target.value);
                        }}
                        className="input-field w-32"
                      >
                        <option value="">Location</option>
                        {LOCATIONS.map(loc => (
                          <option key={loc} value={loc}>{loc}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => handleAssignLocation(item)}
                        disabled={!locationInput}
                        className="btn-primary text-sm flex items-center gap-1"
                      >
                        <MapPin className="w-4 h-4" />
                        Assign
                      </button>
                    </div>
                  )}

                  {/* Step 3: Print Barcode & Accept */}
                  {item.status === 'LOCATED' && (
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => handlePrintBarcode(item)}
                        className="btn-outline text-sm flex items-center gap-1"
                      >
                        <Printer className="w-4 h-4" />
                        Print Barcode
                      </button>
                      <button
                        onClick={() => handleAccept(item)}
                        className="btn-primary text-sm flex items-center gap-1"
                      >
                        <CheckCircle className="w-4 h-4" />
                        Accept Stock
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {pendingItems.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No pending stock to accept</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default StockAcceptance;
