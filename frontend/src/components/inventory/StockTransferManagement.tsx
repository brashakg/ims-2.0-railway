// ============================================================================
// IMS 2.0 - Stock Transfer Management
// ============================================================================
// View, track, and receive stock transfers

import React, { useState, useEffect } from 'react';
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
import api from '../../services/api/client';

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
  // INV-5: backend uses from_location_id / to_location_id (not from_store_id)
  from_location_id: string;
  from_location_name: string;
  to_location_id: string;
  to_location_name: string;
  // Backend status enum is lowercase: draft / pending_approval / approved /
  // in_transit / partially_received / received / completed / cancelled.
  status: string;
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
      // INV-5: backend returns {transfers:[...], total:...} envelope; unwrap it.
      // The getTransfers call uses store_id (convenience param that matches either
      // side) because the backend has no "direction" parameter; incoming/outgoing
      // distinction is done client-side by comparing from_location_id.
      const envelope = await inventoryApi.getTransfers(user.activeStoreId, direction);
      const all: Transfer[] = Array.isArray(envelope)
        ? envelope
        : Array.isArray(envelope?.transfers)
        ? envelope.transfers
        : [];

      // Apply direction filter locally using the real field name.
      let data: Transfer[];
      if (direction === 'outgoing') {
        data = all.filter(t => t.from_location_id === user.activeStoreId);
      } else if (direction === 'incoming') {
        data = all.filter(t => t.to_location_id === user.activeStoreId);
      } else {
        data = all;
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

  const handleReceiveTransfer = async (transferId: string) => {
    if (!selectedTransfer) return;
    setIsReceiving(true);
    try {
      // Build receive payload: mark all items as fully received using their IDs.
      // The backend endpoint is POST /api/v1/transfers/{transfer_id}/receive and
      // expects a list of { transfer_item_id, quantity_received, quantity_damaged }.
      const itemsReceived = selectedTransfer.items.map((item, index) => ({
        transfer_item_id: (item as any).id ?? `item-${index}`,
        quantity_received: item.quantity,
        quantity_damaged: 0,
      }));

      await api.post(`/transfers/${transferId}/receive`, itemsReceived);

      toast.success('Transfer received successfully');
      setShowDetails(false);
      await loadTransfers();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to receive transfer');
    } finally {
      setIsReceiving(false);
    }
  };

  const getStatusBadge = (status: string) => {
    // INV-5: backend status enum is lowercase (draft / pending_approval /
    // approved / in_transit / partially_received / received / completed /
    // cancelled). Map both upper and lower variants so the badge never crashes.
    type StatusColor = 'yellow' | 'blue' | 'purple' | 'green' | 'orange' | 'red' | 'gray';
    const statusConfig: Record<string, { label: string; color: StatusColor; icon: React.ElementType }> = {
      // Uppercase legacy values (kept for backward compatibility)
      PENDING: { label: 'Pending', color: 'yellow', icon: Clock },
      SENT: { label: 'Sent', color: 'blue', icon: ArrowRight },
      IN_TRANSIT: { label: 'In Transit', color: 'purple', icon: Package },
      RECEIVED: { label: 'Received', color: 'green', icon: CheckCircle },
      PARTIALLY_RECEIVED: { label: 'Partially Received', color: 'orange', icon: AlertCircle },
      CANCELLED: { label: 'Cancelled', color: 'red', icon: X },
      // Lowercase values from the backend TransferStatus enum
      draft: { label: 'Draft', color: 'gray', icon: Clock },
      pending_approval: { label: 'Pending Approval', color: 'yellow', icon: Clock },
      approved: { label: 'Approved', color: 'blue', icon: CheckCircle },
      rejected: { label: 'Rejected', color: 'red', icon: X },
      picking: { label: 'Picking', color: 'purple', icon: Package },
      packed: { label: 'Packed', color: 'blue', icon: Package },
      in_transit: { label: 'In Transit', color: 'purple', icon: Package },
      partially_received: { label: 'Partially Received', color: 'orange', icon: AlertCircle },
      received: { label: 'Received', color: 'green', icon: CheckCircle },
      completed: { label: 'Completed', color: 'green', icon: CheckCircle },
      cancelled: { label: 'Cancelled', color: 'red', icon: X },
    };

    const config = statusConfig[status] ?? { label: status, color: 'gray' as StatusColor, icon: Clock };
    const Icon = config.icon;

    const colorClasses: Record<StatusColor, string> = {
      yellow: 'bg-yellow-100 text-yellow-800 border-yellow-200',
      blue: 'bg-blue-100 text-blue-800 border-blue-200',
      purple: 'bg-purple-100 text-purple-800 border-purple-200',
      green: 'bg-green-100 text-green-800 border-green-200',
      orange: 'bg-orange-100 text-orange-800 border-orange-200',
      red: 'bg-red-100 text-red-800 border-red-200',
      gray: 'bg-gray-100 text-gray-800 border-gray-200',
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
    // INV-5: use from_location_id (the actual backend field)
    const isOutgoing = transfer.from_location_id === user?.activeStoreId;
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
            // INV-5: use from_location_id; canReceive covers both lower/uppercase statuses
            const isOutgoing = transfer.from_location_id === user?.activeStoreId;
            const canReceive =
              !isOutgoing &&
              (transfer.status === 'SENT' || transfer.status === 'sent' ||
               transfer.status === 'IN_TRANSIT' || transfer.status === 'in_transit' ||
               transfer.status === 'approved' || transfer.status === 'APPROVED');

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

                    <div className="flex flex-wrap items-center gap-4 text-sm text-gray-600">
                      <div className="flex items-center gap-1.5">
                        <Building2 className="w-4 h-4" />
                        <span>
                          {/* INV-5: use from_location_name / to_location_name */}
                          {isOutgoing ? 'To' : 'From'}: {isOutgoing ? transfer.to_location_name : transfer.from_location_name}
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
                      {/* INV-5: from_location_name is the actual backend field */}
                      {selectedTransfer.from_location_name}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600 mb-1">To Store</p>
                    <p className="font-medium text-gray-900">
                      {selectedTransfer.to_location_name}
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
              <div className="border border-gray-200 rounded-lg overflow-x-auto">
                <table className="w-full min-w-[400px]">
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
                        {/* Handle both upper and lowercase from backend */}
                        {(selectedTransfer.status === 'PARTIALLY_RECEIVED' ||
                          selectedTransfer.status === 'partially_received') && (
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

            {/* Modal Footer — INV-5: use from_location_id; cover both case variants */}
            {selectedTransfer.from_location_id !== user?.activeStoreId &&
              (selectedTransfer.status === 'SENT' || selectedTransfer.status === 'sent' ||
               selectedTransfer.status === 'IN_TRANSIT' || selectedTransfer.status === 'in_transit' ||
               selectedTransfer.status === 'approved' || selectedTransfer.status === 'APPROVED') && (
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
