// ============================================================================
// IMS 2.0 - Stock Transfer Management
// ============================================================================
// View, track, and drive stock transfers through their full backend lifecycle:
// create (elsewhere) -> approve -> ship -> receive (incl. partial) -> complete,
// plus cancel where the backend allows it. Every action here calls the real
// endpoint in routers/transfers.py with its exact contract; button visibility
// mirrors the backend's status + role gates so a user is never shown an action
// the server would 400/403.

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
  Truck,
  Send,
  Ban,
  ThumbsUp,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
// Direct import: barrel re-export can fail to resolve for newly added modules.
import { printDocumentsApi } from '../../services/api/printDocuments';

type TransferDirection = 'outgoing' | 'incoming' | 'all';
// Which sub-flow the details modal is showing.
type ActionMode = 'view' | 'ship' | 'receive';

interface TransferItem {
  // Backend stamps a per-line id (trfi_...) at create — this is what the
  // receive endpoint keys on (transfer_item_id), NOT product_id.
  id?: string;
  product_id: string;
  product_name: string;
  sku: string;
  // Backend line quantities (the old `quantity` field never existed on the
  // server and always rendered blank).
  quantity_requested?: number;
  quantity_shipped?: number;
  quantity_received?: number;
  quantity_damaged?: number;
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
  // picking / packed / in_transit / partially_received / received /
  // completed / cancelled / rejected.
  status: string;
  items: TransferItem[];
  notes?: string;
  created_by: string;
  /** Resolved creator display name (backend stamps username at create). */
  created_by_name?: string;
  created_at: string;
  sent_at?: string;
  received_at?: string;
  total_items: number;
  tracking_number?: string;
  courier_name?: string;
}

// Roles that the backend accepts for each lifecycle endpoint (kept in lockstep
// with routers/transfers.py so the UI never offers a button that will 403).
const APPROVE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'];
const SHIP_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'];
const RECEIVE_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'];
const COMPLETE_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'];
const CANCEL_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'];
const CROSS_STORE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'];

const errMsg = (e: any): string =>
  e?.response?.data?.detail?.message ||
  (typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '') ||
  e?.message ||
  'Action failed';

const lineQty = (item: TransferItem): number =>
  Number(item.quantity_shipped || item.quantity_requested || 0);

export function StockTransferManagement() {
  const { user } = useAuth();
  const toast = useToast();

  const [direction, setDirection] = useState<TransferDirection>('all');
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedTransfer, setSelectedTransfer] = useState<Transfer | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [actionMode, setActionMode] = useState<ActionMode>('view');
  const [actionLoading, setActionLoading] = useState(false);

  // Ship form
  const [trackingNumber, setTrackingNumber] = useState('');
  const [courierName, setCourierName] = useState('');

  // Receive form — per line-id { received, damaged }
  const [receiveLines, setReceiveLines] = useState<
    Record<string, { received: number; damaged: number }>
  >({});

  const roles = user?.roles || [];
  const hasRole = (allowed: string[]) => roles.some((r) => allowed.includes(r));
  const isCrossStore = hasRole(CROSS_STORE_ROLES);
  // Cross-store roles (SUPERADMIN/ADMIN/AREA_MANAGER) bypass the object-level
  // store check on the backend, so from the UI they can act on either side.
  const isSourceSide = (t: Transfer) =>
    isCrossStore || t.from_location_id === user?.activeStoreId;
  const isDestSide = (t: Transfer) =>
    isCrossStore || t.to_location_id === user?.activeStoreId;
  const st = (s: string) => (s || '').toLowerCase();

  const canApprove = (t: Transfer) =>
    hasRole(APPROVE_ROLES) && st(t.status) === 'pending_approval' && isSourceSide(t);
  const canShip = (t: Transfer) =>
    hasRole(SHIP_ROLES) &&
    ['approved', 'packed'].includes(st(t.status)) &&
    isSourceSide(t);
  const canReceive = (t: Transfer) =>
    hasRole(RECEIVE_ROLES) &&
    ['in_transit', 'partially_received'].includes(st(t.status)) &&
    isDestSide(t);
  const canComplete = (t: Transfer) =>
    hasRole(COMPLETE_ROLES) &&
    ['received', 'partially_received'].includes(st(t.status)) &&
    isDestSide(t);
  const canCancel = (t: Transfer) =>
    hasRole(CANCEL_ROLES) &&
    !['completed', 'cancelled', 'in_transit'].includes(st(t.status)) &&
    isSourceSide(t);

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
        data = all.filter((t) => t.from_location_id === user.activeStoreId);
      } else if (direction === 'incoming') {
        data = all.filter((t) => t.to_location_id === user.activeStoreId);
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

  // Open the details modal, optionally jumping straight into a sub-flow.
  const openDetails = (transfer: Transfer, intent: ActionMode = 'view') => {
    setSelectedTransfer(transfer);
    setShowDetails(true);
    if (intent === 'ship') {
      startShip(transfer);
    } else if (intent === 'receive') {
      startReceive(transfer);
    } else {
      setActionMode('view');
    }
  };

  const closeDetails = () => {
    setShowDetails(false);
    setActionMode('view');
    setTrackingNumber('');
    setCourierName('');
    setReceiveLines({});
  };

  const startShip = (_transfer: Transfer) => {
    setTrackingNumber('');
    setCourierName('');
    setActionMode('ship');
  };

  const startReceive = (transfer: Transfer) => {
    // Prefill each line's received qty to what actually shipped (falling back
    // to requested for legacy docs). The backend rejects a received qty that
    // exceeds the shipped qty, so shipped is the correct default ceiling.
    const lines: Record<string, { received: number; damaged: number }> = {};
    transfer.items.forEach((item, index) => {
      const key = item.id ?? `item-${index}`;
      lines[key] = { received: lineQty(item), damaged: 0 };
    });
    setReceiveLines(lines);
    setActionMode('receive');
  };

  // Run a lifecycle action, refresh the list, and keep the modal in sync.
  const runAction = async (
    fn: () => Promise<any>,
    successMsg: string,
    opts?: { close?: boolean },
  ) => {
    setActionLoading(true);
    try {
      const res = await fn();
      toast.success(successMsg);
      if (res?.transfer) setSelectedTransfer(res.transfer as Transfer);
      await loadTransfers();
      if (opts?.close) {
        closeDetails();
      } else {
        setActionMode('view');
      }
      return res;
    } catch (error: any) {
      toast.error(errMsg(error));
      throw error;
    } finally {
      setActionLoading(false);
    }
  };

  const handleApprove = (t: Transfer) =>
    runAction(() => inventoryApi.approveTransfer(t.id, true), 'Transfer approved');

  const handleReject = (t: Transfer) => {
    const reason = window.prompt('Reason for rejecting this transfer?');
    if (!reason || !reason.trim()) return;
    return runAction(
      () => inventoryApi.approveTransfer(t.id, false, reason.trim()),
      'Transfer rejected',
      { close: true },
    );
  };

  const handleShipConfirm = (t: Transfer) =>
    runAction(
      () =>
        inventoryApi.shipTransfer(t.id, {
          trackingNumber: trackingNumber.trim() || undefined,
          courierName: courierName.trim() || undefined,
        }),
      'Transfer shipped',
    );

  const handleReceiveConfirm = (t: Transfer) => {
    const items = t.items.map((item, index) => {
      const key = item.id ?? `item-${index}`;
      const line = receiveLines[key] || { received: 0, damaged: 0 };
      return {
        transfer_item_id: key,
        quantity_received: Number(line.received) || 0,
        quantity_damaged: Number(line.damaged) || 0,
      };
    });
    // Guard client-side too (backend also rejects): damaged <= received.
    const bad = items.find((i) => i.quantity_damaged > i.quantity_received);
    if (bad) {
      toast.error('Damaged quantity cannot exceed received quantity');
      return;
    }
    return runAction(
      () => inventoryApi.receiveTransfer(t.id, items),
      'Transfer received',
    );
  };

  const handleComplete = (t: Transfer) =>
    runAction(() => inventoryApi.completeTransfer(t.id), 'Transfer completed');

  const handleCancel = (t: Transfer) => {
    const reason = window.prompt('Reason for cancelling this transfer?');
    if (!reason || !reason.trim()) return;
    return runAction(
      () => inventoryApi.cancelTransfer(t.id, reason.trim()),
      'Transfer cancelled',
      { close: true },
    );
  };

  const setLine = (key: string, patch: Partial<{ received: number; damaged: number }>) => {
    setReceiveLines((prev) => {
      const current = prev[key] || { received: 0, damaged: 0 };
      return { ...prev, [key]: { ...current, ...patch } };
    });
  };

  // The single most-relevant next action for the list card (opens the modal).
  const primaryCardAction = (
    t: Transfer,
  ): { label: string; intent: ActionMode; icon: React.ElementType } | null => {
    if (canApprove(t)) return { label: 'Approve', intent: 'view', icon: ThumbsUp };
    if (canShip(t)) return { label: 'Ship', intent: 'ship', icon: Truck };
    if (canReceive(t)) return { label: 'Receive', intent: 'receive', icon: Check };
    if (canComplete(t)) return { label: 'Complete', intent: 'view', icon: CheckCircle };
    return null;
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
            const isOutgoing = transfer.from_location_id === user?.activeStoreId;
            const primary = primaryCardAction(transfer);

            return (
              <div
                key={transfer.id}
                className="card hover:shadow-md transition-shadow cursor-pointer"
                onClick={() => openDetails(transfer)}
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
                        openDetails(transfer);
                      }}
                      className="btn-outline text-sm flex items-center gap-2"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                    {primary && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          openDetails(transfer, primary.intent);
                        }}
                        className="btn-primary text-sm flex items-center gap-2"
                      >
                        <primary.icon className="w-4 h-4" />
                        {primary.label}
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
              <div className="flex items-center gap-2">
                <button
                  onClick={async () => {
                    try {
                      await printDocumentsApi.openTransferChallan(selectedTransfer.id);
                    } catch {
                      toast.error('Could not open delivery challan');
                    }
                  }}
                  className="btn-outline flex items-center gap-2 text-sm"
                  title="Print Rule 55 Delivery Challan"
                >
                  <Truck className="w-4 h-4" />
                  Delivery Challan
                </button>
                <button
                  onClick={closeDetails}
                  className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <X className="w-5 h-5 text-gray-500" />
                </button>
              </div>
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
                    <p className="font-medium text-gray-900">{selectedTransfer.created_by_name || selectedTransfer.created_by}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600 mb-1">Total Items</p>
                    <p className="font-medium text-gray-900">
                      {selectedTransfer.total_items} items
                    </p>
                  </div>
                  {selectedTransfer.tracking_number && (
                    <div>
                      <p className="text-sm text-gray-600 mb-1">Tracking</p>
                      <p className="font-medium text-gray-900">
                        {selectedTransfer.tracking_number}
                        {selectedTransfer.courier_name ? ` · ${selectedTransfer.courier_name}` : ''}
                      </p>
                    </div>
                  )}
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

              {/* Ship panel — capture tracking + carrier before shipping */}
              {actionMode === 'ship' && (
                <div className="mb-6 p-4 border border-blue-200 bg-blue-50 rounded-lg space-y-4">
                  <p className="text-sm font-medium text-blue-900 flex items-center gap-2">
                    <Truck className="w-4 h-4" />
                    Shipping details
                  </p>
                  <p className="text-xs text-blue-800">
                    Shipping moves the units out of {selectedTransfer.from_location_name} and
                    marks the transfer In Transit.
                  </p>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-gray-700 mb-1">
                        Tracking Number (optional)
                      </label>
                      <input
                        type="text"
                        value={trackingNumber}
                        onChange={(e) => setTrackingNumber(e.target.value)}
                        placeholder="e.g. AWB123456789"
                        className="input-field w-full"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-700 mb-1">
                        Carrier (optional)
                      </label>
                      <input
                        type="text"
                        value={courierName}
                        onChange={(e) => setCourierName(e.target.value)}
                        placeholder="e.g. Delhivery"
                        className="input-field w-full"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* Items Table */}
              <div className="border border-gray-200 rounded-lg overflow-x-auto">
                <table className="w-full min-w-[520px]">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">
                        Product
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">
                        SKU
                      </th>
                      <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                        {actionMode === 'receive' ? 'Shipped' : 'Qty'}
                      </th>
                      {actionMode === 'receive' ? (
                        <>
                          <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                            Received
                          </th>
                          <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                            Damaged
                          </th>
                        </>
                      ) : (
                        <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">
                          Received
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {selectedTransfer.items.map((item, index) => {
                      const key = item.id ?? `item-${index}`;
                      const shippedCap = lineQty(item);
                      const line = receiveLines[key] || { received: 0, damaged: 0 };
                      return (
                        <tr key={key}>
                          <td className="px-4 py-3 text-sm text-gray-900">
                            {item.product_name}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500">{item.sku}</td>
                          <td className="px-4 py-3 text-sm text-gray-900 text-right">
                            {shippedCap}
                          </td>
                          {actionMode === 'receive' ? (
                            <>
                              <td className="px-4 py-3 text-right">
                                <input
                                  type="number"
                                  min={0}
                                  max={shippedCap}
                                  value={line.received}
                                  onChange={(e) =>
                                    setLine(key, {
                                      received: Math.max(
                                        0,
                                        Math.min(shippedCap, parseInt(e.target.value) || 0),
                                      ),
                                    })
                                  }
                                  className="input-field w-20 text-center"
                                  aria-label={`Received quantity for ${item.product_name}`}
                                />
                              </td>
                              <td className="px-4 py-3 text-right">
                                <input
                                  type="number"
                                  min={0}
                                  max={line.received}
                                  value={line.damaged}
                                  onChange={(e) =>
                                    setLine(key, {
                                      damaged: Math.max(
                                        0,
                                        Math.min(line.received, parseInt(e.target.value) || 0),
                                      ),
                                    })
                                  }
                                  className="input-field w-20 text-center"
                                  aria-label={`Damaged quantity for ${item.product_name}`}
                                />
                              </td>
                            </>
                          ) : (
                            <td className="px-4 py-3 text-sm text-gray-900 text-right">
                              {item.quantity_received || 0}
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Modal Footer — lifecycle actions gated by backend status + role */}
            <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200 bg-gray-50">
              {actionMode === 'ship' ? (
                <>
                  <button
                    onClick={() => setActionMode('view')}
                    disabled={actionLoading}
                    className="btn-outline"
                  >
                    Back
                  </button>
                  <button
                    onClick={() => handleShipConfirm(selectedTransfer)}
                    disabled={actionLoading}
                    className="btn-primary flex items-center gap-2"
                  >
                    {actionLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    Confirm Shipment
                  </button>
                </>
              ) : actionMode === 'receive' ? (
                <>
                  <button
                    onClick={() => setActionMode('view')}
                    disabled={actionLoading}
                    className="btn-outline"
                  >
                    Back
                  </button>
                  <button
                    onClick={() => handleReceiveConfirm(selectedTransfer)}
                    disabled={actionLoading}
                    className="btn-primary flex items-center gap-2"
                  >
                    {actionLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Check className="w-4 h-4" />
                    )}
                    Confirm Receipt
                  </button>
                </>
              ) : (
                <>
                  <button onClick={closeDetails} disabled={actionLoading} className="btn-outline">
                    Close
                  </button>
                  {canCancel(selectedTransfer) && (
                    <button
                      onClick={() => handleCancel(selectedTransfer)}
                      disabled={actionLoading}
                      className="btn-outline text-red-600 border-red-200 hover:bg-red-50 flex items-center gap-2"
                    >
                      <Ban className="w-4 h-4" />
                      Cancel
                    </button>
                  )}
                  {canApprove(selectedTransfer) && (
                    <>
                      <button
                        onClick={() => handleReject(selectedTransfer)}
                        disabled={actionLoading}
                        className="btn-outline text-red-600 border-red-200 hover:bg-red-50 flex items-center gap-2"
                      >
                        <X className="w-4 h-4" />
                        Reject
                      </button>
                      <button
                        onClick={() => handleApprove(selectedTransfer)}
                        disabled={actionLoading}
                        className="btn-primary flex items-center gap-2"
                      >
                        {actionLoading ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <ThumbsUp className="w-4 h-4" />
                        )}
                        Approve
                      </button>
                    </>
                  )}
                  {canShip(selectedTransfer) && (
                    <button
                      onClick={() => startShip(selectedTransfer)}
                      disabled={actionLoading}
                      className="btn-primary flex items-center gap-2"
                    >
                      <Truck className="w-4 h-4" />
                      Ship
                    </button>
                  )}
                  {canReceive(selectedTransfer) && (
                    <button
                      onClick={() => startReceive(selectedTransfer)}
                      disabled={actionLoading}
                      className="btn-primary flex items-center gap-2"
                    >
                      <Check className="w-4 h-4" />
                      Receive
                    </button>
                  )}
                  {canComplete(selectedTransfer) && (
                    <button
                      onClick={() => handleComplete(selectedTransfer)}
                      disabled={actionLoading}
                      className="btn-primary flex items-center gap-2"
                    >
                      {actionLoading ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <CheckCircle className="w-4 h-4" />
                      )}
                      Complete
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
