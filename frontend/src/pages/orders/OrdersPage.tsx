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
} from 'lucide-react';
import type { OrderStatus, PaymentStatus, Order } from '../../types';
import { orderApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { Pagination } from '../../components/common/Pagination';
import clsx from 'clsx';
import { OrderNotificationTracker } from '../../components/orders/OrderNotificationTracker';
import { OrderStatusTimeline } from '../../components/orders/OrderStatusTimeline';

// Status configurations
const ORDER_STATUS_CONFIG: Record<OrderStatus, { label: string; color: string; bgColor: string; icon: typeof Clock }> = {
  DRAFT: { label: 'Draft', color: 'text-gray-600', bgColor: 'bg-gray-100', icon: FileText },
  CONFIRMED: { label: 'Confirmed', color: 'text-blue-600', bgColor: 'bg-blue-100', icon: CheckCircle },
  IN_PROGRESS: { label: 'In Progress', color: 'text-yellow-600', bgColor: 'bg-yellow-100', icon: Clock },
  READY: { label: 'Ready', color: 'text-green-600', bgColor: 'bg-green-100', icon: Package },
  DELIVERED: { label: 'Delivered', color: 'text-emerald-600', bgColor: 'bg-emerald-100', icon: Truck },
  CANCELLED: { label: 'Cancelled', color: 'text-red-600', bgColor: 'bg-red-100', icon: XCircle },
};

const PAYMENT_STATUS_CONFIG: Record<PaymentStatus, { label: string; color: string; bgColor: string }> = {
  PENDING: { label: 'Pending', color: 'text-red-600', bgColor: 'bg-red-100' },
  PARTIAL: { label: 'Partial', color: 'text-yellow-600', bgColor: 'bg-yellow-100' },
  PAID: { label: 'Paid', color: 'text-green-600', bgColor: 'bg-green-100' },
};

export function OrdersPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  // Data state
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<OrderStatus | 'ALL'>('ALL');
  const [dateFilter, setDateFilter] = useState<'today' | 'week' | 'month' | 'all'>('all');

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // Sync status filter from URL query params (e.g. /orders?status=READY)
  useEffect(() => {
    const statusParam = searchParams.get('status');
    if (statusParam && statusParam !== statusFilter) {
      const validStatuses: (OrderStatus | 'ALL')[] = ['ALL', 'DRAFT', 'CONFIRMED', 'IN_PROGRESS', 'READY', 'DELIVERED', 'CANCELLED'];
      if (validStatuses.includes(statusParam as OrderStatus)) {
        setStatusFilter(statusParam as OrderStatus);
      }
    }
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
  const [isDeliveringOrder, setIsDeliveringOrder] = useState(false);

  // Role-based permissions
  const canViewAllStores = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, dateFilter, searchQuery]);

  // Load orders on mount and when filters/page change
  useEffect(() => {
    loadOrders();
  }, [statusFilter, dateFilter, currentPage]);

  const loadOrders = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const params: { storeId?: string; status?: string; date?: string; skip?: number; limit?: number } = {};
      if (!canViewAllStores && user?.activeStoreId) {
        params.storeId = user.activeStoreId;
      }
      if (statusFilter !== 'ALL') {
        params.status = statusFilter;
      }
      if (dateFilter !== 'all') {
        params.date = dateFilter;
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

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(Math.round(amount || 0));
  };

  // Print order invoice — use hidden iframe if popup blocked
  const printOrder = (order: Order) => {
    const items = (order.items || []).map((item: any, i: number) =>
      `<tr><td>${i + 1}</td><td>${item.productName || item.product_name || item.name || 'Item'}</td><td>${item.quantity}</td><td>₹${Math.round(item.unitPrice || item.unit_price || 0).toLocaleString('en-IN')}</td><td>₹${Math.round(item.finalPrice || item.item_total || 0).toLocaleString('en-IN')}</td></tr>`
    ).join('');
    const payments = (order.payments || []).map((p: any) =>
      `<div>${p.mode || p.method}: ₹${Math.round(p.amount).toLocaleString('en-IN')}${p.reference ? ` (${p.reference})` : ''}</div>`
    ).join('');
    const html = `<!DOCTYPE html><html><head><title>Invoice ${order.orderNumber || 'N/A'}</title>
      <style>body{font-family:Arial,sans-serif;padding:20px;max-width:800px;margin:0 auto}
      table{width:100%;border-collapse:collapse;margin:15px 0}th,td{border:1px solid #ddd;padding:8px;text-align:left}
      th{background:#f5f5f5}.total{font-weight:bold;font-size:1.2em}.header{text-align:center;margin-bottom:20px}
      .row{display:flex;justify-content:space-between;padding:4px 0}@media print{button,.no-print{display:none}}</style></head><body>
      <div class="header"><h2>Better Vision Opticals</h2><p>Tax Invoice</p></div>
      <div class="row"><div><strong>Invoice:</strong> ${order.orderNumber || 'N/A'}</div><div><strong>Date:</strong> ${new Date(order.createdAt || '').toLocaleDateString('en-IN')}</div></div>
      <div class="row"><div><strong>Customer:</strong> ${order.customerName || 'Walk-in'}</div><div><strong>Phone:</strong> ${order.customerPhone || '-'}</div></div>
      <table><thead><tr><th>#</th><th>Item</th><th>Qty</th><th>Rate</th><th>Amount</th></tr></thead><tbody>${items}</tbody></table>
      <div style="text-align:right;margin-top:10px">
      <div class="row"><span>Subtotal:</span><span>₹${Math.round(order.subtotal || 0).toLocaleString('en-IN')}</span></div>
      ${(order.totalDiscount || 0) > 0 ? `<div class="row"><span>Discount:</span><span>-₹${Math.round(order.totalDiscount).toLocaleString('en-IN')}</span></div>` : ''}
      <div class="row"><span>Tax:</span><span>₹${Math.round(order.taxAmount || 0).toLocaleString('en-IN')}</span></div>
      <div class="row total"><span>Grand Total:</span><span>₹${Math.round(order.grandTotal || 0).toLocaleString('en-IN')}</span></div>
      <div class="row"><span>Paid:</span><span>₹${Math.round(order.amountPaid || 0).toLocaleString('en-IN')}</span></div>
      ${(order.balanceDue || 0) > 0 ? `<div class="row" style="color:red"><span>Balance Due:</span><span>₹${Math.round(order.balanceDue).toLocaleString('en-IN')}</span></div>` : ''}
      </div>
      ${payments ? `<div style="margin-top:15px"><strong>Payments:</strong>${payments}</div>` : ''}
      <div style="margin-top:30px;text-align:center;color:#666;font-size:12px">Thank you for shopping with Better Vision Opticals</div>
      <button class="no-print" onclick="window.print()" style="display:block;margin:20px auto;padding:10px 30px;background:#c5a55a;color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px">Print</button>
      </body></html>`;

    // Try popup first, fallback to iframe
    const w = window.open('', '_blank', 'width=800,height=600');
    if (w) {
      w.document.write(html);
      w.document.close();
    } else {
      // Popup blocked — use hidden iframe
      const iframe = document.createElement('iframe');
      iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
      document.body.appendChild(iframe);
      const doc = iframe.contentDocument || iframe.contentWindow?.document;
      if (doc) {
        doc.open();
        doc.write(html);
        doc.close();
        setTimeout(() => {
          iframe.contentWindow?.print();
          setTimeout(() => document.body.removeChild(iframe), 1000);
        }, 300);
      }
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
      await orderApi.addPayment(paymentOrder.id, {
        amount,
        mode: paymentMethod,
        reference: paymentReference || undefined,
      });

      toast.success(`Payment of ${formatCurrency(amount)} received`);
      setShowPaymentModal(false);
      setPaymentOrder(null);
      setSelectedOrder(null);
      await loadOrders();
    } catch {
      toast.error('Failed to process payment');
    } finally {
      setIsProcessingPayment(false);
    }
  };

  // Open deliver confirmation modal
  const openDeliverModal = (order: Order) => {
    setDeliverOrder(order);
    setShowDeliverModal(true);
  };

  // Mark order as delivered
  const handleMarkDelivered = async () => {
    if (!deliverOrder) return;

    setIsDeliveringOrder(true);
    try {
      await orderApi.deliverOrder(deliverOrder.id);
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
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Orders</h1>
          <p className="text-gray-500">View and manage all orders</p>
        </div>
        <button
          onClick={loadOrders}
          className="btn-outline flex items-center gap-2"
          disabled={isLoading}
        >
          <RefreshCw className={clsx('w-4 h-4', isLoading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Search and Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by order number, customer name, or phone..."
            />
            {searchQuery.length >= 2 && filteredOrders.length > 0 && filteredOrders.length < orders.length && (
              <div className="absolute z-40 w-full mt-1 bg-gray-800 border border-gray-700 rounded-xl shadow-lg max-h-64 overflow-y-auto">
                {filteredOrders.slice(0, 6).map(order => {
                  const sc = ORDER_STATUS_CONFIG[order.orderStatus as OrderStatus];
                  return (
                    <button key={order.id} onClick={() => { setSelectedOrder(order); setSearchQuery(''); }}
                      className="w-full text-left px-3 py-2.5 hover:bg-bv-gold-50 border-b border-gray-50 last:border-0 flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-white truncate">{order.orderNumber}</p>
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
            >
              <option value="ALL">All Status</option>
              <option value="DRAFT">Draft</option>
              <option value="CONFIRMED">Confirmed</option>
              <option value="IN_PROGRESS">In Progress</option>
              <option value="READY">Ready</option>
              <option value="DELIVERED">Delivered</option>
              <option value="CANCELLED">Cancelled</option>
            </select>
            <select
              value={dateFilter}
              onChange={e => setDateFilter(e.target.value as 'today' | 'week' | 'month' | 'all')}
              className="input-field"
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
              const paymentConfig = PAYMENT_STATUS_CONFIG[order.paymentStatus];
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
                        <p className="font-medium text-white">{order.orderNumber}</p>
                        <div className="flex items-center gap-2 text-sm text-gray-500">
                          <User className="w-3 h-3" />
                          <span>{order.customerName}</span>
                          {order.patientName && order.patientName !== order.customerName && (
                            <span className="text-gray-400">({order.patientName})</span>
                          )}
                        </div>
                        <p className="text-xs text-gray-400">
                          {formatDate(order.createdAt)} at {formatTime(order.createdAt)}
                        </p>
                      </div>
                    </div>

                    {/* Status & Amount */}
                    <div className="text-right">
                      <p className="font-bold text-white">{formatCurrency(order.grandTotal)}</p>
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
          <div className="bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-white">
                  Order {selectedOrder.orderNumber}
                </h2>
                <button
                  onClick={() => setSelectedOrder(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg"
                >
                  <XCircle className="w-5 h-5 text-gray-500" />
                </button>
              </div>

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
                  createdBy={selectedOrder.createdBy}
                />

                {/* Order Status Timeline & Notifications */}
                <OrderNotificationTracker
                  orderId={selectedOrder.id}
                  orderNumber={selectedOrder.orderNumber}
                  customerName={selectedOrder.customerName}
                  customerPhone={selectedOrder.customerPhone}
                  status={selectedOrder.orderStatus as 'DRAFT' | 'CONFIRMED' | 'IN_PROGRESS' | 'READY' | 'DELIVERED' | 'CANCELLED'}
                  createdAt={selectedOrder.createdAt}
                  onSendNotification={(status, channel) => {
                    toast.success(`${channel} notification sent for status: ${status}`);
                  }}
                />

                <div className="flex gap-2 pt-4 flex-wrap">
                  <button
                    onClick={() => printOrder(selectedOrder)}
                    className="btn-primary flex-1 flex items-center justify-center gap-2 min-w-[120px]"
                  >
                    <Printer className="w-4 h-4" />
                    Print Invoice
                  </button>
                  {selectedOrder.orderStatus === 'READY' && selectedOrder.paymentStatus !== 'PENDING' && (
                    <button
                      onClick={() => openDeliverModal(selectedOrder)}
                      className="btn-success flex-1 flex items-center justify-center gap-2 min-w-[150px]"
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

      {/* Payment Collection Modal */}
      {showPaymentModal && paymentOrder && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-800 rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-gray-700">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-white">Collect Payment</h2>
                <button
                  onClick={() => setShowPaymentModal(false)}
                  className="p-2 text-gray-400 hover:text-gray-600 rounded-lg"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="p-6 space-y-4">
              {/* Order Info */}
              <div className="bg-gray-50 p-4 rounded-lg">
                <p className="text-sm text-gray-500">Order</p>
                <p className="font-medium text-white">{paymentOrder.orderNumber}</p>
                <p className="text-sm text-gray-500 mt-1">{paymentOrder.customerName}</p>
                <div className="flex justify-between mt-2 pt-2 border-t border-gray-700">
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
                <div className="grid grid-cols-4 gap-2">
                  {(['CASH', 'CARD', 'UPI', 'BANK_TRANSFER'] as const).map(method => (
                    <button
                      key={method}
                      type="button"
                      onClick={() => setPaymentMethod(method)}
                      className={clsx(
                        'p-2 text-xs rounded-lg border transition-colors',
                        paymentMethod === method
                          ? 'border-bv-gold-600 bg-bv-gold-50 text-bv-gold-700'
                          : 'border-gray-700 hover:border-gray-300'
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

            <div className="p-6 border-t border-gray-700 flex justify-end gap-3">
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
          <div className="bg-gray-800 rounded-xl shadow-xl max-w-md w-full">
            <div className="p-6 border-b border-gray-700">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-white">Mark Order as Delivered?</h2>
                <button
                  onClick={() => setShowDeliverModal(false)}
                  className="p-2 text-gray-400 hover:text-gray-600 rounded-lg"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="p-6 space-y-4">
              {/* Order Info */}
              <div className="bg-gray-50 p-4 rounded-lg">
                <p className="text-sm text-gray-500">Order</p>
                <p className="font-medium text-white">{deliverOrder.orderNumber}</p>
                <p className="text-sm text-gray-500 mt-1">{deliverOrder.customerName}</p>
                <p className="text-sm text-gray-500">{deliverOrder.customerPhone}</p>
                <div className="flex justify-between mt-3 pt-3 border-t border-gray-700">
                  <span className="text-sm text-gray-500">Grand Total:</span>
                  <span className="font-bold text-white">{formatCurrency(deliverOrder.grandTotal || 0)}</span>
                </div>
              </div>

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
