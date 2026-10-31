// ============================================================================
// IMS 2.0 - Purchase Orders section (/purchase/orders)
// ============================================================================
// Wave 1 split: the PO list/create/detail flow moved verbatim out of the old
// PurchaseManagementPage tab container. Renders inside PurchaseLayout.

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Search, Loader2, AlertTriangle } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { usePurchaseShop } from './purchaseShop';
import { vendorsApi } from '../../services/api';
import { PurchaseTable } from './PurchaseTable';
import { PurchaseOrderForm } from './PurchaseOrderForm';
import { PurchaseOrderDetail, type POAction, type POActionOptions } from './PurchaseOrderDetail';
import { useSuppliers, usePurchaseOrdersQuery, purchaseOrdersQueryKey, type PurchaseOrderPage } from './purchaseQueries';
import { lineLabel, mapPOtoPurchaseOrder } from './purchaseMappers';
import type { POStatus, PurchaseOrder } from './purchaseTypes';

// The filter words ARE the badge words (audit F23): the old list offered
// Pending / Approved / Ordered, which the server never produces, and had no
// way to find orders with the vendor. Each word covers its legacy twin.
const STATUS_FILTERS: { value: POStatus; label: string; matches: POStatus[] }[] = [
  { value: 'DRAFT', label: 'Draft', matches: ['DRAFT'] },
  { value: 'SENT', label: 'Sent', matches: ['SENT', 'ACKNOWLEDGED'] },
  { value: 'PARTIALLY_RECEIVED', label: 'Partly received', matches: ['PARTIALLY_RECEIVED', 'PARTIAL'] },
  { value: 'RECEIVED', label: 'Received', matches: ['RECEIVED'] },
  { value: 'CANCELLED', label: 'Cancelled', matches: ['CANCELLED'] },
];

export function PurchaseOrdersSection() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<POStatus | 'ALL'>('ALL');
  const [showCreatePO, setShowCreatePO] = useState(false);
  // ?new=1&product=<id>: a new order for one product (Order stock / Order
  // this now, and the retired Buy Desk's links -- S5).
  const [prefillProductId, setPrefillProductId] = useState<string | undefined>();
  const [selectedPO, setSelectedPO] = useState<PurchaseOrder | null>(null);
  const [editingPO, setEditingPO] = useState<PurchaseOrder | null>(null);

  // Cached across section switches (owner: switching felt like a reload).
  // First visit fetches; later visits render instantly + refresh in background.
  const { storeId, ownStoreId, showShopOf } = usePurchaseShop(); // audit F63: one Purchase scope
  const suppliersQ = useSuppliers();
  const posQ = usePurchaseOrdersQuery(storeId);
  const suppliers = suppliersQ.data ?? [];
  const purchaseOrders = posQ.data?.orders ?? [];
  // Every order in scope, not the page's length (review round 2, #18): the
  // server sends the newest page, and the screen says when it is a cut.
  const totalOrders = posQ.data?.total ?? purchaseOrders.length;
  const isLoading = (suppliersQ.isPending || posQ.isPending);
  const loadError = suppliersQ.isError || posQ.isError
    ? 'Failed to load purchase data'
    : null;

  // Cache writer for PO mutations: the list updates in place, no refetch flash.
  const patchPOs = (fn: (old: PurchaseOrder[]) => PurchaseOrder[]) =>
    queryClient.setQueryData<PurchaseOrderPage>(purchaseOrdersQueryKey(storeId), (old) =>
      old ? { ...old, orders: fn(old.orders) } : old,
    );

  // Header "New PO" button navigates to ?new=1 (see PurchaseLayout).
  useEffect(() => {
    if (searchParams.get('new')) {
      setPrefillProductId(searchParams.get('product') || undefined);
      setShowCreatePO(true);
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      next.delete('product');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const loadData = () => {
    void suppliersQ.refetch();
    void posQ.refetch();
  };

  const statusMatches = STATUS_FILTERS.find((f) => f.value === statusFilter)?.matches;
  const filteredPOs = purchaseOrders.filter(po => {
    const matchesSearch = po.poNumber.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          po.supplierName.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = !statusMatches || statusMatches.includes(po.status);
    return matchesSearch && matchesStatus;
  });

  // Put the server's copy of an order everywhere it shows (list + open modal).
  const showSaved = (updated: PurchaseOrder) => {
    patchPOs(prev => prev.map(p => p.id === updated.id ? updated : p));
    setSelectedPO(updated);
  };

  // ---- PO action handler ----
  // P0-4 (launch gate): state mutates ONLY after the API succeeds, and a
  // refusal is TOASTED, never swallowed. Owner rulings 2026-09-28: there is no
  // approval step (a draft is SENT to the vendor), a draft is editable, and an
  // order or one line is cancelled WITH the reason the person typed -- the
  // server's copy of the order is what the screen then shows.
  const handlePOAction = async (po: PurchaseOrder, action: POAction, opts: POActionOptions = {}) => {
    if (action === 'edit') {
      setSelectedPO(null);
      setEditingPO(po);
      return;
    }
    try {
      if (action === 'send') {
        await vendorsApi.sendPurchaseOrder(po.id);
        showSaved({ ...po, status: 'SENT' });
        toast.success(`${po.poNumber} sent to vendor`);
      } else if (action === 'cancel') {
        const resp = await vendorsApi.cancelPurchaseOrder(po.id, opts.reason ?? '');
        showSaved(resp?.po ? mapPOtoPurchaseOrder(resp.po) : { ...po, status: 'CANCELLED', cancellationReason: opts.reason });
        toast.success(`${po.poNumber} cancelled`);
      } else if (action === 'cancel-line' && opts.lineIndex !== undefined) {
        const line = po.items[opts.lineIndex];
        const saved = await vendorsApi.cancelPurchaseOrderLine(
          po.id, opts.lineIndex, opts.reason ?? '', line?.productId, line?.quantity, po.updatedAt,
        );
        showSaved(mapPOtoPurchaseOrder(saved));
        toast.success(`${line ? lineLabel(line) : 'Line'} cancelled on ${po.poNumber}`);
      }
    } catch (err) {
      toast.error(
        err instanceof Error && err.message
          ? err.message
          : `${po.poNumber} was NOT updated — the server refused the request.`,
      );
    }
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
          aria-label="Filter by status"
          className="input-field w-auto"
        >
          <option value="ALL">All Status</option>
          {STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : (
        <>
          {purchaseOrders.length < totalOrders && (
            <p className="text-xs text-gray-600 mt-3" data-testid="po-list-cut">
              Latest {purchaseOrders.length} of {totalOrders} orders, newest first. Search and the
              status filter look only in these {purchaseOrders.length}.
            </p>
          )}
          <PurchaseTable purchaseOrders={filteredPOs} onViewPO={setSelectedPO} />
        </>
      )}

      {/* Create PO Modal */}
      {showCreatePO && (
        <PurchaseOrderForm
          suppliers={suppliers}
          suppliersLoading={suppliersQ.isPending}
          existingPOCount={totalOrders}
          prefillProductId={prefillProductId}
          onClose={() => {
            setShowCreatePO(false);
            setPrefillProductId(undefined);
          }}
          onCreated={(newPO) => {
            // A new PO delivers to the creator's own shop (W1.4): it joins that
            // shop's list and the all-stores list, never the list of a shop
            // an admin happens to be viewing -- which then shows its shop (F63).
            for (const key of new Set([ownStoreId, undefined])) {
              queryClient.setQueryData<PurchaseOrderPage>(
                purchaseOrdersQueryKey(key),
                (old) => old && { orders: [newPO, ...old.orders], total: old.total + 1 },
              );
            }
            showShopOf(ownStoreId);
            setShowCreatePO(false);
          }}
        />
      )}

      {/* Edit a draft -- the same form, the same pricing rule */}
      {editingPO && (
        <PurchaseOrderForm
          suppliers={suppliers}
          existingPOCount={purchaseOrders.length}
          editing={editingPO}
          onClose={() => setEditingPO(null)}
          onCreated={(saved) => {
            setEditingPO(null);
            showSaved(saved);
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
