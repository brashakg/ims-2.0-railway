// ============================================================================
// IMS 2.0 - Goods Receipt Note (GRN)
// ============================================================================
// GRN creation, partial receipt, quality inspection, barcode scanning, discrepancies

import { useState, useEffect, startTransition } from 'react';
import { Check, AlertCircle, Package, FileText } from 'lucide-react';
import clsx from 'clsx';
import { vendorsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface GRNLineItem {
  product_id: string;
  product_name: string;
  po_qty: number;
  received_qty: number;
  inspection_status: 'pending' | 'passed' | 'failed';
}

interface GRN {
  id: string;
  grn_number: string;
  po_id: string;
  po_number: string;
  received_at: string;
  items_received: number;
  quality_status: 'passed' | 'failed' | 'conditional';
  created_by: string;
}


const INSPECTION_CHECKLIST = [
  'Product packaging intact',
  'Expiry date valid (min 6 months)',
  'No visible damage or scratches',
  'Serial numbers match documentation',
  'Quantity matches PO',
  'Color/specification matches order',
  'Quality certifications present',
];

const getQualityStatusColor = (status: string) => {
  switch (status) {
    case 'passed':
      return 'bg-green-900 text-green-300';
    case 'failed':
      return 'bg-red-900 text-red-300';
    case 'conditional':
      return 'bg-yellow-900 text-yellow-300';
    default:
      return 'bg-gray-700 text-gray-300';
  }
};

export function GoodsReceiptNote() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'create' | 'history' | 'discrepancies'>('create');
  const [poNumber, setPoNumber] = useState('');
  const [receivedItems, setReceivedItems] = useState<GRNLineItem[]>([]);
  const [inspectionChecks, setInspectionChecks] = useState<Record<string, boolean>>({});
  const [qualityNotes, setQualityNotes] = useState('');
  const [discrepancies, setDiscrepancies] = useState('');
  const [grns, setGrns] = useState<GRN[]>([]);
  const [, setIsLoading] = useState(true);

  // Load GRNs on mount
  useEffect(() => {
    const loadGRNs = async () => {
      try {
        setIsLoading(true);
        const storeId = user?.activeStoreId || '';
        const response = await vendorsApi.getGRNs({ store_id: storeId });
        const grnList = Array.isArray(response) ? response : response.data || [];
        const transformedGRNs = grnList.map((grn: any) => ({
          id: grn.id || grn._id,
          grn_number: grn.grn_number,
          po_id: grn.po_id,
          po_number: grn.po_number || 'Unknown PO',
          received_at: grn.received_at,
          items_received: grn.items_received || 0,
          quality_status: grn.quality_status || 'passed',
          created_by: grn.created_by || 'Unknown',
        }));
        setGrns(transformedGRNs);
      } catch (error) {
        toast.error('Failed to load GRNs');
      } finally {
        setIsLoading(false);
      }
    };

    loadGRNs();
  }, [user?.activeStoreId]);

  const toggleInspectionCheck = (item: string) => {
    startTransition(() => {
      setInspectionChecks(prev => ({
        ...prev,
        [item]: !prev[item],
      }));
    });
  };

  const allChecksComplete = Object.values(inspectionChecks).every(v => v);
  const qualityStatus = allChecksComplete && !discrepancies ? 'passed' : discrepancies ? 'failed' : 'conditional';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Goods Receipt Notes</h1>
          <p className="text-gray-400">Record item receipt with quality inspection</p>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total GRNs</p>
          <p className="text-2xl font-bold text-white">{grns.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Quality Passed</p>
          <p className="text-2xl font-bold text-green-400">
            {grns.filter(g => g.quality_status === 'passed').length}
          </p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Conditional Receipts</p>
          <p className="text-2xl font-bold text-yellow-400">
            {grns.filter(g => g.quality_status === 'conditional').length}
          </p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Failed Quality</p>
          <p className="text-2xl font-bold text-red-400">
            {grns.filter(g => g.quality_status === 'failed').length}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['create', 'history', 'discrepancies'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => startTransition(() => setActiveTab(tab))}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'create' ? 'Create GRN' : tab === 'history' ? 'History' : 'Discrepancies'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'create' && (
        <div className="space-y-6">
          {/* PO Selection */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4">Select Purchase Order</h3>
            <select
              value={poNumber}
              onChange={(e) => setPoNumber(e.target.value)}
              className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white"
            >
              <option value="PO-2024-001">PO-2024-001 - Optical Frames Ltd</option>
              <option value="PO-2024-002">PO-2024-002 - Lens Manufacturers Inc</option>
            </select>
          </div>

          {/* Items Reception */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Package className="w-5 h-5" />
              Items Received
            </h3>

            <div className="space-y-4">
              {receivedItems.map((item, idx) => (
                <div key={idx} className="bg-gray-700 rounded-lg p-4">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <p className="text-white font-semibold">{item.product_name}</p>
                      <p className="text-gray-400 text-sm">Product ID: {item.product_id}</p>
                    </div>
                    <span className={clsx(
                      'px-2 py-1 rounded text-xs font-semibold',
                      item.inspection_status === 'passed' ? 'bg-green-900 text-green-300' :
                      item.inspection_status === 'failed' ? 'bg-red-900 text-red-300' :
                      'bg-yellow-900 text-yellow-300'
                    )}>
                      {item.inspection_status === 'passed' ? 'Passed' : item.inspection_status === 'failed' ? 'Failed' : 'Pending'}
                    </span>
                  </div>

                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <p className="text-gray-400 text-xs mb-2">PO Quantity</p>
                      <p className="text-white font-semibold">{item.po_qty}</p>
                    </div>
                    <div>
                      <p className="text-gray-400 text-xs mb-2">Received Quantity</p>
                      <input
                        type="number"
                        value={item.received_qty}
                        onChange={(e) => {
                          startTransition(() => {
                            const newItems = [...receivedItems];
                            newItems[idx].received_qty = parseInt(e.target.value) || 0;
                            setReceivedItems(newItems);
                          });
                        }}
                        className="w-full px-2 py-1 bg-gray-600 border border-gray-500 rounded text-white text-sm"
                      />
                    </div>
                    <div>
                      <p className="text-gray-400 text-xs mb-2">Variance</p>
                      <p className={clsx(
                        'text-sm font-semibold',
                        item.received_qty === item.po_qty ? 'text-green-400' : 'text-orange-400'
                      )}>
                        {item.received_qty - item.po_qty > 0 ? '+' : ''}{item.received_qty - item.po_qty}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Quality Inspection */}
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Check className="w-5 h-5" />
              Quality Inspection Checklist
            </h3>

            <div className="space-y-3 mb-4">
              {INSPECTION_CHECKLIST.map((item) => (
                <label key={item} className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={inspectionChecks[item] || false}
                    onChange={() => toggleInspectionCheck(item)}
                    className="w-4 h-4 rounded border-gray-500"
                  />
                  <span className="text-gray-300 text-sm">{item}</span>
                </label>
              ))}
            </div>

            <div className="mb-4">
              <label className="block text-gray-400 text-sm mb-2">Quality Notes</label>
              <textarea
                value={qualityNotes}
                onChange={(e) => startTransition(() => setQualityNotes(e.target.value))}
                placeholder="Add any observations during inspection..."
                className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 resize-none"
                rows={3}
              />
            </div>

            <div className="mb-4">
              <label className="block text-gray-400 text-sm mb-2">Discrepancies Found</label>
              <textarea
                value={discrepancies}
                onChange={(e) => startTransition(() => setDiscrepancies(e.target.value))}
                placeholder="List any damaged items, missing items, or other discrepancies..."
                className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 resize-none"
                rows={3}
              />
            </div>

            <div className={clsx(
              'p-4 rounded-lg',
              qualityStatus === 'passed' ? 'bg-green-900/30 border border-green-700' :
              qualityStatus === 'failed' ? 'bg-red-900/30 border border-red-700' :
              'bg-yellow-900/30 border border-yellow-700'
            )}>
              <p className={clsx(
                'text-sm font-semibold flex items-center gap-2',
                qualityStatus === 'passed' ? 'text-green-300' :
                qualityStatus === 'failed' ? 'text-red-300' :
                'text-yellow-300'
              )}>
                <Check className="w-4 h-4" />
                Quality Status: {qualityStatus.charAt(0).toUpperCase() + qualityStatus.slice(1)}
              </p>
            </div>
          </div>

          {/* Submit Button */}
          <button className="w-full px-4 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center justify-center gap-2">
            <FileText className="w-5 h-5" />
            Create GRN
          </button>
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-4">
          {grns.map((grn) => (
            <div key={grn.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-gray-600 transition-colors">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="text-white font-semibold">{grn.grn_number}</p>
                  <p className="text-gray-400 text-sm">Against {grn.po_number}</p>
                </div>
                <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', getQualityStatusColor(grn.quality_status))}>
                  {grn.quality_status === 'passed' ? 'Passed' : grn.quality_status === 'failed' ? 'Failed' : 'Conditional'}
                </span>
              </div>

              <div className="grid grid-cols-4 gap-4 mb-3 pb-3 border-b border-gray-700">
                <div>
                  <p className="text-gray-400 text-xs mb-1">Items Received</p>
                  <p className="text-white font-semibold">{grn.items_received}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Received Date</p>
                  <p className="text-white font-semibold">{new Date(grn.received_at).toLocaleDateString()}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Received Time</p>
                  <p className="text-white font-semibold">{new Date(grn.received_at).toLocaleTimeString()}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Created By</p>
                  <p className="text-white font-semibold">{grn.created_by}</p>
                </div>
              </div>

              <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold">
                View Details
              </button>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'discrepancies' && (
        <div className="space-y-4">
          <div className="bg-blue-900/30 border border-blue-700 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-blue-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-blue-300 font-semibold">Discrepancy Report</p>
              <p className="text-blue-300 text-sm mt-1">
                Items with variance between PO quantity and received quantity, or quality inspection failures.
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <div className="bg-orange-900/30 border border-orange-700 rounded-lg p-4">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <p className="text-orange-300 font-semibold">GRN-2024-002 - Frame Model A</p>
                  <p className="text-gray-400 text-sm">Against PO-2024-001</p>
                </div>
                <span className="px-2 py-1 bg-orange-900 text-orange-300 rounded text-xs font-semibold">
                  Quantity Variance
                </span>
              </div>
              <p className="text-orange-300 text-sm">Expected: 100 units | Received: 95 units | Variance: -5 units</p>
            </div>

            <div className="bg-red-900/30 border border-red-700 rounded-lg p-4">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <p className="text-red-300 font-semibold">GRN-2024-003 - Lens Coating</p>
                  <p className="text-gray-400 text-sm">Against PO-2024-002</p>
                </div>
                <span className="px-2 py-1 bg-red-900 text-red-300 rounded text-xs font-semibold">
                  Quality Failure
                </span>
              </div>
              <p className="text-red-300 text-sm">Defect: Packaging damaged, 12 units affected, return authorization issued.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default GoodsReceiptNote;
