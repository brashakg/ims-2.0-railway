// ============================================================================
// IMS 2.0 - Orders Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Search,
  FileText,
  Clock,
  CheckCircle,
  XCircle,
  Truck,
  Package,
  User,
  CreditCard,
  Eye,
  Printer,
  RefreshCw,
  Loader2,
  AlertCircle,
  CheckCheck,
  Pencil,
} from 'lucide-react';
import type { OrderStatus, PaymentStatus, Order } from '../../types';
import { orderApi } from '../../services/api';
import { marketingApi } from '../../services/api/marketing';
// Direct import: barrel re-export can fail to resolve for newly added modules.
import { printDocumentsApi } from '../../services/api/printDocuments';
import { formatDateIST, formatTimeIST } from '../../utils/datetime';
import { useAuth } from '../../context/AuthContext';
import { canCloseHandover } from './handoverRoles';
import { useToast } from '../../context/ToastContext';
import { Pagination } from '../../components/common/Pagination';
import clsx from 'clsx';
import { OrderNotificationTracker } from '../../components/orders/OrderNotificationTracker';
import { OrderStatusTimeline } from '../../components/orders/OrderStatusTimeline';
import { OrderShippingCard } from '../../components/orders/OrderShippingCard';
import { OrderTrackingQR } from '../../components/orders/OrderTrackingQR';
import { SuperadminOrderEditModal } from '../../components/orders/SuperadminOrderEditModal';

// Status configurations
const ORDER_STATUS_CONFIG: Record<OrderStatus, { label: string; color: string; bgColor: string; icon: typeof Clock }> = {
  DRAFT: { label: 'Draft', color: 'text-gray-600', bgColor: 'bg-gray-100', icon: FileText },
  CONFIRMED: { label: 'Confirmed', color: 'text-blue-700', bgColor: 'bg-blue-50', icon: CheckCircle },
  PROCESSING: { label: 'Processing', color: 'text-amber-700', bgColor: 'bg-amber-50', icon: Clock },
  READY: { label: 'Ready', color: 'text-green-700', bgColor: 'bg-green-50', icon: Package },
  DELIVERED: { label: 'Delivered', color: 'text-green-700', bgColor: 'bg-green-50', icon: Truck },
  CANCELLED: { label: 'Cancelled', color: 'text-red-700', bgColor: 'bg-red-50', icon: XCircle },
};

const PAYMENT_STATUS_CONFIG: Record<PaymentStatus, { label: string; color: string; bgColor: string }> = {
  PENDING: { label: 'Pending', color: 'text-red-700', bgColor: 'bg-red-50' },
  PARTIAL: { label: 'Partial', color: 'text-amber-700', bgColor: 'bg-amber-50' },
  PAID: { label: 'Paid', color: 'text-green-700', bgColor: 'bg-green-50' },
  UNPAID: { label: 'Unpaid', color: 'text-red-700', bgColor: 'bg-red-50' },
  CREDIT: { label: 'On Credit', color: 'text-amber-700', bgColor: 'bg-amber-50' },
  REFUNDED: { label: 'Refunded', color: 'text-gray-600', bgColor: 'bg-gray-100' },
};

// Clinical Rx FLAG-AND-HOLD (owner decision 2026-06-30): an online spectacle
// order missing a valid prescription is booked but held (rx_pending +
// fulfillment_hold). The backend REJECTS marking it ready/delivered/shipped
// until an admin clears the hold, so we surface WHY the transition is blocked.
// The generic orders payload carries the flags straight through order_to_frontend
// (snake_case); tolerate a camelCase variant too. This mirrors the OnlineOrdersPage
// Rx-hold chip (#947), rebuilt here for the generic orders page.
const isOnRxHold = (order: Order): boolean => {
  const o = order as unknown as {
    rx_pending?: boolean;
    fulfillment_hold?: boolean;
    rxPending?: boolean;
    fulfillmentHold?: boolean;
  };
  return Boolean(o.rx_pending || o.fulfillment_hold || o.rxPending || o.fulfillmentHold);
};

// A stock-miss hold (paid online order whose units could not be claimed)
// rides the same fulfillment_hold flag but is NOT an Rx problem — labelling
// it "Rx hold" sends staff after a prescription that was never the issue.
// Mirrors the backend classifier (orders.order_hold_kinds), including the
// legacy shape where the stock reason rode rx_hold_reason.
const isOnStockHold = (order: Order): boolean => {
  if (!isOnRxHold(order)) return false;
  const o = order as unknown as {
    stock_hold_reason?: string;
    rx_hold_reason?: string;
    rxHoldReason?: string;
  };
  return Boolean(
    o.stock_hold_reason ||
      String(o.rx_hold_reason || o.rxHoldReason || '').startsWith(
        'Stock could not be claimed',
      ),
  );
};

const hasRxPending = (order: Order): boolean => {
  const o = order as unknown as { rx_pending?: boolean; rxPending?: boolean };
  return Boolean(o.rx_pending || o.rxPending);
};

/** Short label naming the active hold(s): "Rx hold" / "stock hold" / both. */
const holdLabel = (order: Order): string => {
  const stock = isOnStockHold(order);
  if (!stock) return 'Rx hold';
  return hasRxPending(order) ? 'Rx + stock hold' : 'Stock hold';
};

const rxHoldReason = (order: Order): string => {
  const o = order as unknown as {
    rx_hold_reason?: string;
    rxHoldReason?: string;
    stock_hold_reason?: string;
  };
  return [String(o.rx_hold_reason || o.rxHoldReason || ''), String(o.stock_hold_reason || '')]
    .filter(Boolean)
    .join(' · ');
};

export function OrdersPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  // Data state
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  // Public tracking token for the open order's QR. New orders already carry it
  // in the list payload; for older orders we lazily fetch the full order (the
  // backend mints + persists a token on GET) so the QR can still render.
  const [trackingToken, setTrackingToken] = useState<string | null>(null);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<OrderStatus | 'ALL'>('ALL');
  const [dateFilter, setDateFilter] = useState<'today' | 'week' | 'month' | 'all'>('all');

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // Sync status filter from URL query params (e.g. /orders?status=READY)
  // Also honour order_id deep-links from the global command palette so the
  // list pre-filters to the picked order instead of dumping the user on the
  // full unfiltered list.
  useEffect(() => {
    const statusParam = searchParams.get('status');
    if (statusParam && statusParam !== statusFilter) {
      const validStatuses: (OrderStatus | 'ALL')[] = ['ALL', 'DRAFT', 'CONFIRMED', 'PROCESSING', 'READY', 'DELIVERED', 'CANCELLED'];
      if (validStatuses.includes(statusParam as OrderStatus)) {
        setStatusFilter(statusParam as OrderStatus);
      }
    }
    const orderIdParam = searchParams.get('order_id');
    if (orderIdParam && orderIdParam !== searchQuery) {
      setSearchQuery(orderIdParam);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Payment modal state
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [paymentOrder, setPaymentOrder] = useState<Order | null>(null);
  const [paymentAmount, setPaymentAmount] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<'CASH' | 'CARD' | 'UPI' | 'BANK_TRANSFER'>('CASH');
  const [paymentReference, setPaymentReference] = useState('');
  const [isProcessingPayment, setIsProcessingPayment] = useState(false);

  // Deliver confirmation modal state
  const [showDeliverModal, setShowDeliverModal] = useState(false);
  const [deliverOrder, setDeliverOrder] = useState<Order | null>(null);
  // Credit-delivery gate (owner ruling): delivering with a balance due needs
  // a manager, or a manager-approved token pasted here by other roles.
  const [deliverToken, setDeliverToken] = useState('');
  const [isDeliveringOrder, setIsDeliveringOrder] = useState(false);

  // Build item #16 — post-creation order/invoice edit. Owner decision 2026-06-19:
  // available to SUPERADMIN and ADMIN (still a privileged, audited override).
  const canEditOrder = (user?.roles || []).some((r) => r === 'SUPERADMIN' || r === 'ADMIN');
  const [showSuperadminEdit, setShowSuperadminEdit] = useState(false);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, dateFilter, searchQuery]);

  // Load orders on mount and when filters / page / SELECTED STORE change.
  // The activeStoreId dep is load-bearing — without it, the topbar's
  // store-switch updates user.activeStoreId in AuthContext but this page's
  // data stays pinned to the old store. (May 2026 store-switch reactivity fix.)
  useEffect(() => {
    loadOrders();
  }, [statusFilter, dateFilter, currentPage, user?.activeStoreId]);

  // Resolve the open order's public tracking token for the QR. Use the value
  // already on the row if present; otherwise fetch the full order (the backend
  // mints a token on GET for orders that predate the feature). Fail-soft.
  useEffect(() => {
    if (!selectedOrder) {
      setTrackingToken(null);
      return;
    }
    const onRow = selectedOrder.trackingToken || selectedOrder.tracking_token;
    if (onRow) {
      setTrackingToken(onRow);
      return;
    }
    let cancelled = false;
    setTrackingToken(null);
    orderApi
      .getOrder(selectedOrder.id)
      .then((full: { trackingToken?: string; tracking_token?: string }) => {
        if (!cancelled) setTrackingToken(full?.trackingToken || full?.tracking_token || null);
      })
      .catch(() => {
        /* fail-soft: QR just won't render */
      });
    return () => {
      cancelled = true;
    };
  }, [selectedOrder]);

  const loadOrders = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const params: { storeId?: string; status?: string; from_date?: string; skip?: number; limit?: number } = {};
      // Always scope to the topbar-selected store. The selection lives in
      // user.activeStoreId for every role (HQ admins included) — previously
      // this was gated to non-HQ roles, so an admin switching the topbar to
      // Pune still saw their home store's orders (the backend fell back to
      // the token's active_store_id).
      if (user?.activeStoreId) {
        params.storeId = user.activeStoreId;
      }
      if (statusFilter !== 'ALL') {
        params.status = statusFilter;
      }
      if (dateFilter !== 'all') {
        // Backend orders-list takes from_date/to_date (YYYY-MM-DD), not ?date=.
        // Map the chosen window to a start date; the repo filters created_at >=
        // from_date (today / start-of-week / start-of-month), which covers all
        // three options without needing to_date.
        const now = new Date();
        const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        if (dateFilter === 'week') {
          start.setDate(start.getDate() - 6);
        } else if (dateFilter === 'month') {
          start.setMonth(start.getMonth() - 1);
        }
        const y = start.getFullYear();
        const m = String(start.getMonth() + 1).padStart(2, '0');
        const d = String(start.getDate()).padStart(2, '0');
        params.from_date = `${y}-${m}-${d}`;
      }
      params.skip = (currentPage - 1) * pageSize;
      params.limit = pageSize;
      const response = await orderApi.getOrders(params);
      setOrders(response.orders || response || []);
    } catch {
      setError('Failed to load orders. Please try again.');
      setOrders([]);
    } finally {
      setIsLoading(false);
    }
  };

  // Filter orders locally by search
  const filteredOrders = orders.filter(order => {
    const matchesSearch = !searchQuery ||
      order.orderNumber?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      order.customerName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      order.customerPhone?.includes(searchQuery);

    return matchesSearch;
  });

  // Paginate filtered results (client-side slice for local search)
  const paginatedOrders = filteredOrders.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  // IST-correct: backend timestamps are naive UTC; format in Asia/Kolkata.
  const formatDate = (dateStr: string) => formatDateIST(dateStr);
  const formatTime = (dateStr: string) => formatTimeIST(dateStr);

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(Math.round(amount || 0));
  };

  // Print order invoice — use hidden iframe if popup blocked
  // Open the server-rendered Rule 55 Delivery Challan (goods moving to the
  // customer). The endpoint is JWT-protected + returns HTML; the service
  // fetches with the auth header and opens it in a new tab for printing.
  const printChallan = async (order: Order) => {
    try {
      await printDocumentsApi.openOrderChallan(order.id);
    } catch (e) {
      toast.error('Could not open delivery challan');
    }
  };

  const printOrder = async (order: Order) => {
    // THE STATUTORY INVOICE IS THE SERVER'S DOCUMENT, AND ONLY THAT.
    //
    // This used to hand-build an HTML page titled "Tax Invoice" and print the
    // ORDER number under an "Invoice:" label -- so a customer could be handed
    // paper carrying a number that exists nowhere in the books. Under Indian
    // GST the invoice serial is a consecutive series per financial year, minted
    // once and recorded; a number assembled in a browser cannot be that. The
    // totals were client-assembled here too, beside a server that already holds
    // the persisted per-line tax.
    //
    // It is the third renderer of this defect class to be retired: the POS
    // GSTInvoice modal invented a BV/FY/store serial outright, and the receipt
    // preview's A4 tab hard-coded 9/9/18 tax labels. One invoice document now,
    // fetched from the same door the till uses, so there is nothing left to
    // drift. Note the sibling below, printChallan, was already doing this.
    try {
      await printDocumentsApi.openOrderInvoice(order.id);
    } catch (e: any) {
      // An error body on a blob request is itself a Blob, so the server detail
      // is not readable here -- name the usual cause instead of guessing.
      toast.error(
        e?.message || 'Could not open the tax invoice. Check the store GSTIN in settings.',
      );
    }
  };

  // Open payment modal
  const openPaymentModal = (order: Order) => {
    setPaymentOrder(order);
    setPaymentAmount(String(Math.round((order.balanceDue || 0) * 100) / 100));
    setPaymentMethod('CASH');
    setPaymentReference('');
    setShowPaymentModal(true);
  };

  // Process payment
  const handlePayment = async () => {
    if (!paymentOrder) return;

    const amount = parseFloat(paymentAmount);
    if (isNaN(amount) || amount <= 0) {
      toast.error('Please enter a valid amount');
      return;
    }
    if (amount > (paymentOrder.balanceDue || 0)) {
      toast.error('Amount cannot exceed balance due');
      return;
    }

    setIsProcessingPayment(true);
    try {
      // Backend PaymentCreate expects `method`, not `mode` — frontend
      // type was inherited from legacy Payment shape. Sending `mode` was
      // causing a pydantic 422 → "Failed to process payment" for every
      // collection. Send both to survive older callers.
      await orderApi.addPayment(paymentOrder.id, {
        amount,
        // Send both for compatibility — backend accepts either (6.13)
        method: paymentMethod,
        mode: paymentMethod,
        reference: paymentReference || undefined,
      } as any);

      toast.success(`Payment of ${formatCurrency(amount)} received`);
      setShowPaymentModal(false);
      setPaymentOrder(null);
      setSelectedOrder(null);
      await loadOrders();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[Orders] addPayment failed:', e);
      const msg = (e as any)?.response?.data?.detail || (e as Error)?.message || 'Failed to process payment';
      toast.error(typeof msg === 'string' ? msg : 'Failed to process payment');
    } finally {
      setIsProcessingPayment(false);
    }
  };

  // Open deliver confirmation modal
  const openDeliverModal = (order: Order) => {
    setDeliverOrder(order);
    setDeliverToken('');
    setShowDeliverModal(true);
  };

  // Mark order as delivered
  const handleMarkDelivered = async () => {
    if (!deliverOrder) return;

    setIsDeliveringOrder(true);
    try {
      await orderApi.deliverOrder(
        deliverOrder.id,
        deliverToken.trim() ? { approval_token: deliverToken.trim() } : undefined
      );
      toast.success('Order marked as delivered');
      setShowDeliverModal(false);
      setDeliverOrder(null);
      setSelectedOrder(null);
      await loadOrders();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Failed to mark order as delivered');
    } finally {
      setIsDeliveringOrder(false);
    }
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Orders</div>
          <h1>What's in flight.</h1>
          <div className="hint">Draft · confirmed · workshop · ready · delivered. Status moves only with role-gated transitions.</div>
        </div>
        <button
          onClick={loadOrders}
          className="btn sm"
          disabled={isLoading}
        >
          <RefreshCw className={clsx('w-4 h-4', isLoading && 'animate-spin')} /> Refresh
        </button>
      </div>

      {/* Search and Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by order number, customer name, or phone..."
            />
            {searchQuery.length >= 2 && filteredOrders.length > 0 && filteredOrders.length < orders.length && (
              <div className="absolute z-40 w-full mt-1 bg-white border border-gray-200 rounded-xl shadow-lg max-h-64 overflow-y-auto">
                {filteredOrders.slice(0, 6).map(order => {
                  const sc = ORDER_STATUS_CONFIG[order.orderStatus as OrderStatus];
                  return (
                    <button key={order.id} onClick={() => { setSelectedOrder(order); setSearchQuery(''); }}
                      className="w-full text-left px-3 py-2.5 hover:bg-bv-red-50 border-b border-gray-50 last:border-0 flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{order.orderNumber}</p>
                        <p className="text-xs text-gray-500">{order.customerName} {order.customerPhone ? `· ${order.customerPhone}` : ''}</p>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <p className="text-sm font-bold">{formatCurrency(order.grandTotal)}</p>
                        <span className={clsx('text-[10px] px-1.5 py-0.5 rounded-full', sc?.bgColor, sc?.color)}>{sc?.label}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as OrderStatus | 'ALL')}
              className="input-field"
              title="Filter orders by status"
            >
              <option value="ALL">All Status</option>
              <option value="DRAFT">Draft</option>
              <option value="CONFIRMED">Confirmed</option>
              <option value="PROCESSING">Processing</option>
              <option value="READY">Ready</option>
              <option value="DELIVERED">Delivered</option>
              <option value="CANCELLED">Cancelled</option>
            </select>
            <select
              value={dateFilter}
              onChange={e => setDateFilter(e.target.value as 'today' | 'week' | 'month' | 'all')}
              className="input-field"
              title="Filter orders by date range"
            >
              <option value="all">All Time</option>
              <option value="today">Today</option>
              <option value="week">This Week</option>
              <option value="month">This Month</option>
            </select>
          </div>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertCircle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadOrders} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Orders List */}
      <div className="card">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : filteredOrders.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>{searchQuery ? 'No orders found matching your search' : 'No orders yet'}</p>
          </div>
        ) : (
          <>
          <div className="divide-y divide-gray-200">
            {paginatedOrders.map(order => {
              const statusConfig = ORDER_STATUS_CONFIG[order.orderStatus];
              // Fallback so an unmapped/legacy status still renders a badge
              // (was blank for UNPAID/CREDIT/REFUNDED before they were mapped).
              const paymentConfig =
                PAYMENT_STATUS_CONFIG[order.paymentStatus] ||
                { label: order.paymentStatus || '—', color: 'text-gray-600', bgColor: 'bg-gray-100' };
              const StatusIcon = statusConfig?.icon || FileText;

              return (
                <div
                  key={order.id}
                  className="p-4 hover:bg-gray-50 transition-colors cursor-pointer"
                  onClick={() => setSelectedOrder(order)}
                >
                  <div className="flex items-start justify-between gap-4">
                    {/* Order Info */}
                    <div className="flex items-start gap-4">
                      <div className={clsx(
                        'w-10 h-10 rounded-lg flex items-center justify-center',
                        statusConfig?.bgColor || 'bg-gray-100'
                      )}>
                        <StatusIcon className={clsx('w-5 h-5', statusConfig?.color || 'text-gray-600')} />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{order.orderNumber}</p>
                        <div className="flex items-center gap-2 text-sm text-gray-500">
                          <User className="w-3 h-3" />
                          <span>{order.customerName}</span>
                          {order.patientName && order.patientName !== order.customerName && (
                            <span className="text-gray-500">({order.patientName})</span>
                          )}
                        </div>
                        <p className="text-xs text-gray-500">
                          {formatDate(order.createdAt)} at {formatTime(order.createdAt)}
                        </p>
                      </div>
                    </div>

                    {/* Status & Amount */}
                    <div className="text-right">
                      <p className="font-bold text-gray-900">{formatCurrency(order.grandTotal)}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={clsx(
                          'text-xs px-2 py-0.5 rounded-full',
                          statusConfig?.bgColor,
                          statusConfig?.color
                        )}>
                          {statusConfig?.label}
                        </span>
                        <span className={clsx(
                          'text-xs px-2 py-0.5 rounded-full',
                          paymentConfig?.bgColor,
                          paymentConfig?.color
                        )}>
                          {paymentConfig?.label}
                        </span>
                        {isOnRxHold(order) && (
                          <span
                            className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 inline-flex items-center gap-1"
                            title={`On ${holdLabel(order).toLowerCase()} - clear the hold before marking it delivered/ready`}
                          >
                            <AlertCircle className="w-3 h-3" />
                            {holdLabel(order)}
                          </span>
                        )}
                      </div>
                      {order.balanceDue > 0 && (
                        <p className="text-xs text-red-600 mt-1">
                          Due: {formatCurrency(order.balanceDue)}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Items Preview */}
                  <div className="mt-2 ml-14 text-sm text-gray-500">
                    {order.items?.length || 0} item{(order.items?.length || 0) !== 1 ? 's' : ''}
                  </div>

                  {/* Quick Actions */}
                  <div className="mt-3 ml-14 flex items-center gap-2">
                    <button
                      onMouseDown={(e) => { e.preventDefault(); (document.activeElement as HTMLElement)?.blur?.(); }}
                      onClick={(e) => { e.stopPropagation(); setSelectedOrder(order); }}
                      className="text-xs text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-3 h-3" />
                      View
                    </button>
                    <button
                      onMouseDown={(e) => { e.preventDefault(); (document.activeElement as HTMLElement)?.blur?.(); }}
                      onClick={(e) => { e.stopPropagation(); printOrder(order); }}
                      className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Printer className="w-3 h-3" />
                      Print
                    </button>
                    {order.paymentStatus !== 'PAID' && (
                      <button
                        onMouseDown={(e) => { e.preventDefault(); (document.activeElement as HTMLElement)?.blur?.(); }}
                        onClick={(e) => { e.stopPropagation(); openPaymentModal(order); }}
                        className="text-xs text-green-600 hover:text-green-700 flex items-center gap-1"
                      >
                        <CreditCard className="w-3 h-3" />
                        Collect Payment
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <Pagination
            currentPage={currentPage}
            totalItems={filteredOrders.length}
            pageSize={pageSize}
            onPageChange={setCurrentPage}
          />
          </>
        )}
      </div>

      {/* Order Detail Modal - Placeholder */}
      {selectedOrder && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90dvh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">
                  Order {selectedOrder.orderNumber}
                </h2>
                <button
                  onClick={() => setSelectedOrder(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg"
                  aria-label="Close"
                  title="Close"
                >
                  <XCircle className="w-5 h-5 text-gray-500" />
                </button>
              </div>

              {/* Rx flag-and-hold banner: explains why this order can't be
                  marked ready / delivered / shipped until an admin clears it. */}
              {isOnRxHold(selectedOrder) && (
                <div className="mb-4 bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-amber-700 mt-0.5 flex-shrink-0" />
                  <div className="text-sm text-amber-800">
                    <span className="font-medium">On {holdLabel(selectedOrder).toLowerCase()}.</span>{' '}
                    {isOnStockHold(selectedOrder) && !hasRxPending(selectedOrder)
                      ? 'This order is paid but its stock could not be claimed (oversell); it cannot be marked ready, delivered, or shipped until the stock is resolved and an admin clears the hold.'
                      : isOnStockHold(selectedOrder)
                        ? 'This order is missing a valid prescription AND its stock could not be claimed; it cannot be marked ready, delivered, or shipped until an admin clears the holds.'
                        : 'This order is missing a valid prescription and cannot be marked ready, delivered, or shipped until an admin clears the hold.'}
                    {rxHoldReason(selectedOrder) && (
                      <span className="block text-amber-700 mt-0.5">{rxHoldReason(selectedOrder)}</span>
                    )}
                  </div>
                </div>
              )}

              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-gray-500">Customer</p>
                    <p className="font-medium">{selectedOrder.customerName}</p>
                    <p className="text-sm text-gray-500">{selectedOrder.customerPhone}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Status</p>
                    <p className="font-medium">{ORDER_STATUS_CONFIG[selectedOrder.orderStatus]?.label}</p>
                    <p className="text-sm text-gray-500">{PAYMENT_STATUS_CONFIG[selectedOrder.paymentStatus]?.label}</p>
                  </div>
                </div>

                <div>
                  <p className="text-sm text-gray-500 mb-2">Items</p>
                  <div className="bg-gray-50 rounded-lg p-3">
                    {selectedOrder.items?.map((item, index) => (
                      <div key={index} className="flex justify-between py-1 text-sm">
                        <span>{item.productName} x{item.quantity}</span>
                        <span className="font-medium">{formatCurrency(item.finalPrice)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="border-t pt-4">
                  <div className="flex justify-between text-sm">
                    <span>Subtotal</span>
                    <span>{formatCurrency(selectedOrder.subtotal)}</span>
                  </div>
                  {selectedOrder.totalDiscount > 0 && (
                    <div className="flex justify-between text-sm text-green-600">
                      <span>Discount</span>
                      <span>-{formatCurrency(selectedOrder.totalDiscount)}</span>
                    </div>
                  )}
                  <div className="flex justify-between text-sm">
                    <span>Tax</span>
                    <span>{formatCurrency(selectedOrder.taxAmount)}</span>
                  </div>
                  <div className="flex justify-between font-bold mt-2 pt-2 border-t">
                    <span>Grand Total</span>
                    <span>{formatCurrency(selectedOrder.grandTotal)}</span>
                  </div>
                  <div className="flex justify-between text-sm mt-2">
                    <span>Amount Paid</span>
                    <span>{formatCurrency(selectedOrder.amountPaid)}</span>
                  </div>
                  {selectedOrder.balanceDue > 0 && (
                    <div className="flex justify-between text-sm text-red-600">
                      <span>Balance Due</span>
                      <span>{formatCurrency(selectedOrder.balanceDue)}</span>
                    </div>
                  )}
                </div>

                {/* Status Timeline */}
                <OrderStatusTimeline
                  statusHistory={selectedOrder.statusHistory}
                  createdAt={selectedOrder.createdAt}
                  createdBy={selectedOrder.createdByName || selectedOrder.createdBy}
                />

                {/* Order Status Timeline & Notifications */}
                <OrderNotificationTracker
                  orderId={selectedOrder.id}
                  orderNumber={selectedOrder.orderNumber}
                  customerName={selectedOrder.customerName}
                  customerPhone={selectedOrder.customerPhone}
                  status={selectedOrder.orderStatus as 'DRAFT' | 'CONFIRMED' | 'PROCESSING' | 'READY' | 'DELIVERED' | 'CANCELLED'}
                  createdAt={selectedOrder.createdAt}
                  onSendNotification={async (status, channel) => {
                    // Was a fake toast with NO API call — a silent lie to
                    // staff. Queues through the audited MSG91 pipeline now.
                    const template = (
                      {
                        CONFIRMED: 'ORDER_CONFIRMED',
                        READY: 'ORDER_READY',
                        DELIVERED: 'ORDER_DELIVERED',
                      } as Record<string, string>
                    )[status];
                    if (!template) return;
                    try {
                      await marketingApi.sendNotification({
                        customer_id: selectedOrder.customerId,
                        customer_phone: selectedOrder.customerPhone || '',
                        customer_name: selectedOrder.customerName,
                        template_id: template,
                        channel,
                        variables: { order_number: selectedOrder.orderNumber },
                        category: 'SERVICE',
                      });
                      toast.success(`${channel} notification queued for ${selectedOrder.customerName}`);
                    } catch (err: any) {
                      toast.error(err?.response?.data?.detail || 'Failed to queue notification');
                    }
                  }}
                />

                {/* Thank-you + Google review link (separate send per owner spec) */}
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await marketingApi.sendReviewRequest(selectedOrder.id);
                      toast.success('Thank-you + review link queued on WhatsApp');
                    } catch (err: any) {
                      toast.error(err?.response?.data?.detail || 'Failed to send review request');
                    }
                  }}
                  className="w-full inline-flex items-center justify-center gap-2 text-sm font-medium text-green-700 bg-green-50 hover:bg-green-100 border border-green-200 rounded-lg px-3 py-2 transition-colors"
                >
                  Send thank-you + review link (WhatsApp)
                </button>

                {/* Shipping (Shiprocket) — book + track customer shipments */}
                <OrderShippingCard
                  orderId={selectedOrder.id}
                  orderNumber={selectedOrder.orderNumber}
                  storeId={selectedOrder.storeId}
                  balanceDue={selectedOrder.balanceDue}
                  paymentStatus={selectedOrder.paymentStatus}
                />

                {/* Customer order-tracking QR — public, no-login link. */}
                <OrderTrackingQR
                  trackingToken={trackingToken}
                  orderNumber={selectedOrder.orderNumber}
                />

                <div className="flex gap-2 pt-4 flex-wrap">
                  {/* Build item #16: SUPERADMIN-only post-creation edit. Hidden
                      for terminal CANCELLED orders (nothing to correct). */}
                  {canEditOrder && selectedOrder.orderStatus !== 'CANCELLED' && (
                    <button
                      onClick={() => setShowSuperadminEdit(true)}
                      className="btn-outline flex-1 flex items-center justify-center gap-2 min-w-[120px]"
                    >
                      <Pencil className="w-4 h-4" />
                      Edit order
                    </button>
                  )}
                  <button
                    onClick={() => printOrder(selectedOrder)}
                    className="btn-primary flex-1 flex items-center justify-center gap-2 min-w-[120px]"
                  >
                    <Printer className="w-4 h-4" />
                    Print Invoice
                  </button>
                  <button
                    onClick={() => printChallan(selectedOrder)}
                    className="btn-outline flex-1 flex items-center justify-center gap-2 min-w-[150px]"
                  >
                    <Truck className="w-4 h-4" />
                    Delivery Challan
                  </button>
                  {/* Hidden for roles the backend's HANDOVER_ROLES refuses. This
                      button 403'd for OPTOMETRIST while rendering fully enabled
                      with no role condition -- a green control that fails in
                      front of the customer with no in-app escalation. The
                      backend stays the authority; this only stops offering an
                      action it will reject. */}
                  {canCloseHandover(user) && selectedOrder.orderStatus === 'READY' && selectedOrder.paymentStatus !== 'PENDING' && (
                    <button
                      onClick={() => openDeliverModal(selectedOrder)}
                      disabled={isOnRxHold(selectedOrder)}
                      title={
                        isOnRxHold(selectedOrder)
                          ? `On ${holdLabel(selectedOrder).toLowerCase()} - clear the hold before marking it delivered/ready`
                          : undefined
                      }
                      className={clsx(
                        'btn-success flex-1 flex items-center justify-center gap-2 min-w-[150px]',
                        isOnRxHold(selectedOrder) && 'opacity-50 cursor-not-allowed'
                      )}
                    >
                      <CheckCheck className="w-4 h-4" />
                      Mark Delivered
                    </button>
                  )}
                  {selectedOrder.balanceDue > 0 && (
                    <button
                      onClick={() => openPaymentModal(selectedOrder)}
                      className="btn-outline flex-1 flex items-center justify-center gap-2 min-w-[150px]"
                    >
                      <CreditCard className="w-4 h-4" />
                      Collect Payment
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Build item #16 — SUPERADMIN post-creation order edit modal */}
      {showSuperadminEdit && selectedOrder && canEditOrder && (
        <SuperadminOrderEditModal
          order={selectedOrder}
          onClose={() => setShowSuperadminEdit(false)}
          onSaved={async () => {
            setShowSuperadminEdit(false);
            setSelectedOrder(null);
            await loadOrders();
          }}
        />
      )}

      {/* Payment Collection Modal */}
      {showPaymentModal && paymentOrder && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-gray-900">Collect Payment</h2>
                <button
                  onClick={() => setShowPaymentModal(false)}
                  className="p-2 text-gray-500 hover:text-gray-700 rounded-lg"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="p-6 space-y-4">
              {/* Order Info */}
              <div className="bg-gray-50 p-4 rounded-lg">
                <p className="text-sm text-gray-500">Order</p>
                <p className="font-medium text-gray-900">{paymentOrder.orderNumber}</p>
                <p className="text-sm text-gray-500 mt-1">{paymentOrder.customerName}</p>
                <div className="flex justify-between mt-2 pt-2 border-t border-gray-200">
                  <span className="text-sm text-gray-500">Balance Due:</span>
                  <span className="font-bold text-red-600">{formatCurrency(paymentOrder.balanceDue || 0)}</span>
                </div>
              </div>

              {/* Amount */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Amount <span className="text-red-500">*</span>
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
                  <input
                    type="number"
                    value={paymentAmount}
                    onChange={e => setPaymentAmount(e.target.value)}
                    placeholder="0"
                    min="0"
                    max={paymentOrder.balanceDue || 0}
                    className="input-field w-full pl-8"
                  />
                </div>
                <div className="flex gap-2 mt-2">
                  <button
                    type="button"
                    onClick={() => setPaymentAmount(String(Math.round((paymentOrder.balanceDue || 0) * 100) / 100))}
                    className="text-xs px-2 py-1 bg-gray-100 rounded hover:bg-gray-200"
                  >
                    Full Amount
                  </button>
                  <button
                    type="button"
                    onClick={() => setPaymentAmount(String(Math.round((paymentOrder.balanceDue || 0) / 2 * 100) / 100))}
                    className="text-xs px-2 py-1 bg-gray-100 rounded hover:bg-gray-200"
                  >
                    50%
                  </button>
                </div>
              </div>

              {/* Payment Method */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Payment Method
                </label>
                <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2">
                  {(['CASH', 'CARD', 'UPI', 'BANK_TRANSFER'] as const).map(method => (
                    <button
                      key={method}
                      type="button"
                      onClick={() => setPaymentMethod(method)}
                      className={clsx(
                        'p-2 text-xs rounded-lg border transition-colors',
                        paymentMethod === method
                          ? 'border-bv-red-600 bg-bv-red-50 text-bv-red-700'
                          : 'border-gray-200 hover:border-gray-300'
                      )}
                    >
                      {method === 'BANK_TRANSFER' ? 'Bank' : method}
                    </button>
                  ))}
                </div>
              </div>

              {/* Reference */}
              {paymentMethod !== 'CASH' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Reference / Transaction ID
                  </label>
                  <input
                    type="text"
                    value={paymentReference}
                    onChange={e => setPaymentReference(e.target.value)}
                    placeholder="Enter reference number"
                    className="input-field w-full"
                  />
                </div>
              )}
            </div>

            <div className="p-6 border-t border-gray-200 flex justify-end gap-3">
              <button
                onClick={() => setShowPaymentModal(false)}
                className="btn-secondary"
                disabled={isProcessingPayment}
              >
                Cancel
              </button>
              <button
                onClick={handlePayment}
                className="btn-primary flex items-center gap-2"
                disabled={isProcessingPayment}
              >
                {isProcessingPayment ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Processing...
                  </>
                ) : (
                  <>
                    <CreditCard className="w-4 h-4" />
                    Collect {paymentAmount ? formatCurrency(parseFloat(paymentAmount)) : '₹0'}
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mark Delivered Confirmation Modal */}
      {showDeliverModal && deliverOrder && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-gray-900">Mark Order as Delivered?</h2>
                <button
                  onClick={() => setShowDeliverModal(false)}
                  className="p-2 text-gray-500 hover:text-gray-700 rounded-lg"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="p-6 space-y-4">
              {/* Order Info */}
              <div className="bg-gray-50 p-4 rounded-lg">
                <p className="text-sm text-gray-500">Order</p>
                <p className="font-medium text-gray-900">{deliverOrder.orderNumber}</p>
                <p className="text-sm text-gray-500 mt-1">{deliverOrder.customerName}</p>
                <p className="text-sm text-gray-500">{deliverOrder.customerPhone}</p>
                <div className="flex justify-between mt-3 pt-3 border-t border-gray-200">
                  <span className="text-sm text-gray-500">Grand Total:</span>
                  <span className="font-bold text-gray-900">{formatCurrency(deliverOrder.grandTotal || 0)}</span>
                </div>
                {(deliverOrder.balanceDue || 0) > 0 && (
                  <div className="flex justify-between mt-1">
                    <span className="text-sm font-medium text-red-600">Balance Due:</span>
                    <span className="font-bold text-red-600">{formatCurrency(deliverOrder.balanceDue || 0)}</span>
                  </div>
                )}
              </div>

              {/* Credit-delivery gate: a balance still due needs a manager */}
              {(deliverOrder.balanceDue || 0) > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
                  <p className="text-sm text-amber-800">
                    <strong>{formatCurrency(deliverOrder.balanceDue || 0)} is still due.</strong>{' '}
                    Collect it first (Record Payment), or deliver on credit — managers
                    can deliver directly; other roles need a manager-approved token.
                  </p>
                  <input
                    type="text"
                    value={deliverToken}
                    onChange={(e) => setDeliverToken(e.target.value)}
                    placeholder="Manager approval token (if you are not a manager)"
                    className="input-field text-sm"
                  />
                </div>
              )}

              {/* Confirmation message */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                <p className="text-sm text-blue-800">
                  This order will be marked as delivered. Make sure the customer has received all items and the payment is collected.
                </p>
              </div>

              {/* Action buttons */}
              <div className="flex gap-2 pt-4">
                <button
                  onClick={() => setShowDeliverModal(false)}
                  className="btn-outline flex-1"
                  disabled={isDeliveringOrder}
                >
                  Cancel
                </button>
                <button
                  onClick={handleMarkDelivered}
                  className="btn-primary flex-1 flex items-center justify-center gap-2"
                  disabled={isDeliveringOrder}
                >
                  {isDeliveringOrder ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Marking...
                    </>
                  ) : (
                    <>
                      <CheckCheck className="w-4 h-4" />
                      Confirm Delivery
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default OrdersPage;
