// ============================================================================
// IMS 2.0 - Stock Transfer Management
// ============================================================================
// View, track, and receive stock transfers

import { useState, useEffect } from 'react';
import {
  ArrowRightLeft,
  ArrowRight,
  ArrowLeft,
  Package,
  CheckCircle,
  Clock,
  X,
  AlertCircle,
  Loader2,
  Eye,
  Check,
  Building2,
  Calendar,
  Filter,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';

type TransferDirection = 'outgoing' | 'incoming' | 'all';

interface TransferItem {
  product_id: string;
  product_name: string;
  sku: string;
  quantity: number;
  quantity_received?: number;
}

interface Transfer {
  id: string;
  transfer_number: string;
  from_store_id: string;
  from_store_name: string;
  to_store_id: string;
  to_store_name: string;
  status: 'PENDING' | 'SENT' | 'IN_TRANSIT' | 'RECEIVED' | 'PARTIALLY_RECEIVED' | 'CANCELLED';
  items: TransferItem[];
  notes?: string;
  created_by: string;
  created_at: string;
  sent_at?: string;
  received_at?: string;
  total_items: number;
}

export function StockTransferManagement() {
  const { user } = useAuth();
  const toast = useToast();

  const [direction, setDirection] = useState<TransferDirection>('all');
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedTransfer, setSelectedTransfer] = useState<Transfer | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [isReceiving, setIsReceiving] = useState(false);

  useEffect(() => {
    loadTransfers();
  }, [direction, user?.activeStoreId]);

  const loadTransfers = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    try {
      let data;
      if (direction === 'all') {
        // Fetch both incoming and outgoing
        const [incoming, outgoing] = await Promise.all([
          inventoryApi.getTransfers(user.activeStoreId, 'incoming'),
          inventoryApi.getTransfers(user.activeStoreId, 'outgoing'),
        ]);
        data = [...incoming, ...outgoing];
      } else {
        data = await inventoryApi.getTransfers(user.activeStoreId, direction);
      }
      setTransfers(data);
    } catch (error: any) {
      toast.error('Failed to load transfers');
    } finally {
      setIsLoading(false);
    }
  };

  const handleViewDetails = (transfer: Transfer) => {
    setSelectedTransfer(transfer);
    setShowDetails(true);
  };

  const handleReceiveTransfer = async (_transferId: string) => {
    setIsReceiving(true);
    try {
      // In production, call API to mark transfer as received
      // await inventoryApi.receiveTransfer(transferId);
      await new Promise(resolve => setTimeout(resolve, 1000)); // Mock delay

      toast.success('Transfer received successfully');
      setShowDetails(false);
      await loadTransfers();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to receive transfer');
    } finally {
      setIsReceiving(false);
    }
  };

  const getStatusBadge = (status: Transfer['status']) => {
    const statusConfig = {
      PENDING: { label: 'Pending', color: 'yellow' as const, icon: Clock },
      SENT: { label: 'Sent', color: 'blue' as const, icon: ArrowRight },
      IN_TRANSIT: { label: 'In Transit', color: 'purple' as const, icon: Package },
      RECEIVED: { label: 'Received', color: 'green' as const, icon: CheckCircle },
      PARTIALLY_RECEIVED: { label: 'Partially Received', color: 'orange' as const, icon: AlertCircle },
      CANCELLED: { label: 'Cancelled', color: 'red' as const, icon: X },
    };

    const config = statusConfig[status];
    const Icon = config.icon;

    const colorClasses: Record<typeof config.color, string> = {
      yellow: 'bg-yellow-100 text-yellow-800 border-yellow-200',
      blue: 'bg-blue-100 text-blue-800 border-blue-200',
      purple: 'bg-purple-100 text-purple-800 border-purple-200',
      green: 'bg-green-100 text-green-800 border-green-200',
      orange: 'bg-orange-100 text-orange-800 border-orange-200',
      red: 'bg-red-100 text-red-800 border-red-200',
    };

    return (
      <span
        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${
          colorClasses[config.color]
        }`}
      >
        <Icon className="w-3.5 h-3.5" />
        {config.label}
      </span>
    );
  };

  const getDirectionIcon = (transfer: Transfer) => {
    const isOutgoing = transfer.from_store_id === user?.activeStoreId;
    return isOutgoing ? (
      <ArrowRight className="w-5 h-5 text-red-500" />
    ) : (
      <ArrowLeft className="w-5 h-5 text-green-500" />
    );
  };

  const filteredTransfers = transfers;

  return (
    <div className="space-y-4">
      {/* Header with Filters */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Filter className="w-5 h-5 text-gray-500" />
          <span className="text-sm font-medium text-gray-700">Direction:</span>
          <div className="flex bg-gray-100 rounded-lg p-1">
            <button
              onClick={() => setDirection('all')}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                direction === 'all'
                  ? 'bg-white text-purple-600 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              All
            </button>
            <button
              onClick={() => setDirection('outgoing')}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                direction === 'outgoing'
                  ? 'bg-white text-purple-600 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Outgoing
            </button>
            <button
              onClick={() => setDirection('incoming')}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                direction === 'incoming'
                  ? 'bg-white text-purple-600 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Incoming
            </button>
          </div>
        </div>
      </div>

      {/* Transfers List */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredTransfers.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <ArrowRightLeft className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No transfers found</p>
          <p className="text-sm">
            {direction === 'outgoing'
              ? 'No outgoing transfers'
              : direction === 'incoming'
              ? 'No incoming transfers'
              : 'No transfers recorded yet'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredTransfers.map((transfer) => {
            const isOutgoing = transfer.from_store_id === user?.activeStoreId;
            const canReceive =
              !isOutgoing &&
              (transfer.status === 'SENT' || transfer.status === 'IN_TRANSIT');

            return (
              <div
                key={transfer.id}
                className="card hover:shadow-md transition-shadow cursor-pointer"
                onClick={() => handleViewDetails(transfer)}
              >
                <div className="flex items-center gap-4">
                  {/* Direction Icon */}
                  <div className="flex-shrink-0">{getDirectionIcon(transfer)}</div>

                  {/* Transfer Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-1">
                      <span className="font-mono text-sm font-medium text-gray-900">
                        #{transfer.transfer_number}
                      </span>
                      {getStatusBadge(transfer.status)}
                    </div>

                    <div className="flex items-center gap-4 text-sm text-gray-600">
                      <div className="flex items-center gap-1.5">
                        <Building2 className="w-4 h-4" />
                        <span>
                          {isOutgoing ? 'To' : 'From'}: {isOutgoing ? transfer.to_store_name : transfer.from_store_name}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Package className="w-4 h-4" />
                        <span>{transfer.total_items} items</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Calendar className="w-4 h-4" />
                        <span>{new Date(transfer.created_at).toLocaleDateString()}</span>
                      </div>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleViewDetails(transfer);
                      }}
                      className="btn-outline text-sm flex items-center gap-2"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                    {canReceive && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleReceiveTransfer(transfer.id);
                        }}
                        className="btn-primary text-sm flex items-center gap-2"
                      >
                        <Check className="w-4 h-4" />
                        Receive
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Transfer Details Modal */}
      {showDetails && selectedTransfer && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[90vh] overflow-hidden">
            {/* Modal Header */}
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  Transfer #{selectedTransfer.transfer_number}
                </h2>
                <p className="text-sm text-gray-500 mt-1">
                  Created on {new Date(selectedTransfer.created_at).toLocaleString()}
                </p>
              </div>
              <button
                onClick={() => setShowDetails(false)}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-6 overflow-y-auto" style={{ maxHeight: 'calc(90vh - 200px)' }}>
              {/* Status and Details */}
              <div className="grid grid-cols-2 gap-6 mb-6">
                <div className="space-y-4">
                  <div>
                    <p className="text-sm text-gray-600 mb-1">Status</p>
                    {getStatusBadge(selectedTransfer.status)}
                  </div>
                  <div>
                    <p className="text-sm text-gray-600 mb-1">From Store</p>
                    <p className="font-medium text-gray-900">
                      {selectedTransfer.from_store_name}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600 mb-1">To Store</p>
                    <p className="font-medium text-gray-900">
                      {selectedTransfer.to_store_name}
                    </p>
                  </div>
                </div>
                <div className="space-y-4">
                  <div>
                    <p className="text-sm text-gray-600 mb-1">Created By</p>
                    <p className="font-medium text-gray-900">{selectedTransfer.created_by}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600 mb-1">Total Items</p>
                    <p className="font-medium text-gray-900">
                      {selectedTransfer.total_items} items
                    </p>
                  </div>
                  {selectedTransfer.received_at && (
                    <div>
                      <p className="text-sm text-gray-600 mb-1">Received At</p>
                      <p className="font-medium text-gray-900">
                        {new Date(selectedTransfer.received_at).toLocaleString()}
                      </p>
                    </div>
                  )}
                </div>
              </div>

              {/* Notes */}
              {selectedTransfer.notes && (
                <div className="mb-6 p-4 bg-gray-50 rounded-lg">
                  <p className="text-sm text-gray-600 mb-1">Notes:</p>
                  <p className="text-sm text-gray-900">{selectedTransfer.notes}</p>
                </div>
              )}

              {/* Items Table */}
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">
                        Product
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">
                        SKU
                      </th>
                      <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                        Quantity
                      </th>
                      {selectedTransfer.status === 'PARTIALLY_RECEIVED' && (
                        <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                          Received
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {selectedTransfer.items.map((item, index) => (
                      <tr key={index}>
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {item.product_name}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">{item.sku}</td>
                        <td className="px-4 py-3 text-sm text-gray-900 text-right">
                          {item.quantity}
                        </td>
                        {selectedTransfer.status === 'PARTIALLY_RECEIVED' && (
                          <td className="px-4 py-3 text-sm text-gray-900 text-right">
                            {item.quantity_received || 0}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Modal Footer */}
            {selectedTransfer.from_store_id !== user?.activeStoreId &&
              (selectedTransfer.status === 'SENT' ||
                selectedTransfer.status === 'IN_TRANSIT') && (
                <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200 bg-gray-50">
                  <button
                    onClick={() => setShowDetails(false)}
                    disabled={isReceiving}
                    className="btn-outline"
                  >
                    Close
                  </button>
                  <button
                    onClick={() => handleReceiveTransfer(selectedTransfer.id)}
                    disabled={isReceiving}
                    className="btn-primary flex items-center gap-2"
                  >
                    {isReceiving ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Receiving...
                      </>
                    ) : (
                      <>
                        <Check className="w-4 h-4" />
                        Receive Transfer
                      </>
                    )}
                  </button>
                </div>
              )}
          </div>
        </div>
      )}
    </div>
  );
}
