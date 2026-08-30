// ============================================================================
// IMS 2.0 - Purchase Orders section (/purchase/orders)
// ============================================================================
// Wave 1 split: the PO list/create/detail flow moved verbatim out of the old
// PurchaseManagementPage tab container. Renders inside PurchaseLayout.

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search, Loader2, AlertTriangle } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi } from '../../services/api';
import { PurchaseTable } from './PurchaseTable';
import { PurchaseOrderForm } from './PurchaseOrderForm';
import { PurchaseOrderDetail } from './PurchaseOrderDetail';
import { mapVendorToSupplier, mapPOtoPurchaseOrder } from './purchaseMappers';
import type { POStatus, Supplier, PurchaseOrder } from './purchaseTypes';

export function PurchaseOrdersSection() {
  const toast = useToast();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<POStatus | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [showCreatePO, setShowCreatePO] = useState(false);
  const [selectedPO, setSelectedPO] = useState<PurchaseOrder | null>(null);

  // Header "New PO" button navigates to ?new=1 (see PurchaseLayout).
  useEffect(() => {
    if (searchParams.get('new')) {
      setShowCreatePO(true);
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    loadData();
    // Refetch POs when the topbar store changes (loadData scopes by activeStoreId).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId]);

  const loadData = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const storeId = user?.activeStoreId;

      const [vendorsResp, posResp] = await Promise.all([
        vendorsApi.getVendors({ is_active: true }),
        vendorsApi.getPurchaseOrders(storeId ? { store_id: storeId } : {}),
      ]);

      const rawVendors: unknown[] = vendorsResp?.vendors ?? [];
      const rawPOs: unknown[] = posResp?.purchase_orders ?? [];

      setSuppliers(rawVendors.map(mapVendorToSupplier));
      setPurchaseOrders(rawPOs.map(mapPOtoPurchaseOrder));
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to load purchase data';
      setLoadError(msg);
      toast.error('Failed to load purchase data');
    } finally {
      setIsLoading(false);
    }
  };

  const filteredPOs = purchaseOrders.filter(po => {
    const matchesSearch = po.poNumber.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          po.supplierName.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || po.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // ---- PO Status Action handler ----
  const handlePOAction = async (po: PurchaseOrder, action: string) => {
    let newStatus: POStatus = po.status;
    let message = '';

    try {
      switch (action) {
        case 'submit':
          // Backend uses 'send' to transition DRAFT -> SENT; map to PENDING for UI
          await vendorsApi.sendPurchaseOrder(po.id);
          newStatus = 'PENDING';
          message = `${po.poNumber} submitted for approval`;
          break;
        case 'approve':
          newStatus = 'APPROVED';
          message = `${po.poNumber} approved`;
          break;
        case 'reject':
          await vendorsApi.cancelPurchaseOrder(po.id, 'Rejected by approver');
          newStatus = 'CANCELLED';
          message = `${po.poNumber} rejected`;
          break;
        case 'order':
          newStatus = 'ORDERED';
          message = `${po.poNumber} marked as ordered`;
          break;
        case 'receive':
          newStatus = 'RECEIVED';
          message = `${po.poNumber} marked as received`;
          break;
        default:
          return;
      }
    } catch {
      // If API call fails, still update local state optimistically for non-critical actions
      // (approve/order/receive don't have dedicated status-change endpoints yet)
    }

    const updatedPO: PurchaseOrder = {
      ...po,
      status: newStatus,
      ...(action === 'approve' ? { approvedBy: 'Current User' } : {}),
      ...(action === 'receive' ? { receivedDate: new Date().toISOString().split('T')[0] } : {}),
    };

    setPurchaseOrders(prev => prev.map(p => p.id === po.id ? updatedPO : p));
    setSelectedPO(updatedPO);
    toast.success(message);
  };

  return (
    <>
      {/* Load Error Banner */}
      {loadError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-900">Failed to load data</p>
            <p className="text-xs text-red-700 mt-1">{loadError}</p>
          </div>
          <button
            onClick={loadData}
            className="text-xs font-medium text-red-700 hover:text-red-900 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Search & Filters */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
          <input
            type="text"
            placeholder="Search by PO number or supplier..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as POStatus | 'ALL')}
          className="input-field w-auto"
        >
          <option value="ALL">All Status</option>
          <option value="DRAFT">Draft</option>
          <option value="PENDING">Pending</option>
          <option value="APPROVED">Approved</option>
          <option value="ORDERED">Ordered</option>
          <option value="RECEIVED">Received</option>
          <option value="CANCELLED">Cancelled</option>
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : (
        <PurchaseTable purchaseOrders={filteredPOs} onViewPO={setSelectedPO} />
      )}

      {/* Create PO Modal */}
      {showCreatePO && (
        <PurchaseOrderForm
          suppliers={suppliers}
          existingPOCount={purchaseOrders.length}
          onClose={() => setShowCreatePO(false)}
          onCreated={(newPO) => {
            setPurchaseOrders(prev => [newPO, ...prev]);
            setShowCreatePO(false);
          }}
        />
      )}

      {/* PO Detail Modal */}
      {selectedPO && (
        <PurchaseOrderDetail
          po={selectedPO}
          onClose={() => setSelectedPO(null)}
          onAction={handlePOAction}
        />
      )}
    </>
  );
}
