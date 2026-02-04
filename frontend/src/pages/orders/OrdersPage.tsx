// ============================================================================
// IMS 2.0 - Orders Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
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
} from 'lucide-react';
import type { OrderStatus, PaymentStatus, Order } from '../../types';
import { orderApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

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

  // Data state
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<OrderStatus | 'ALL'>('ALL');
  const [dateFilter, setDateFilter] = useState<'today' | 'week' | 'month' | 'all'>('all');

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Role-based permissions
  const canViewAllStores = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);
  const _canRefund = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']);
  // Reserved for future refund functionality
  void _canRefund;

  // Load orders on mount
  useEffect(() => {
    loadOrders();
  }, [statusFilter, dateFilter]);

  const loadOrders = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const params: { storeId?: string; status?: string; date?: string } = {};
      if (!canViewAllStores && user?.activeStoreId) {
        params.storeId = user.activeStoreId;
      }
      if (statusFilter !== 'ALL') {
        params.status = statusFilter;
      }
      if (dateFilter !== 'all') {
        params.date = dateFilter;
      }
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
    }).format(amount);
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Orders</h1>
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
          <div className="divide-y divide-gray-200">
            {filteredOrders.map(order => {
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
                        <p className="font-medium text-gray-900">{order.orderNumber}</p>
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
                      onClick={(e) => { e.stopPropagation(); setSelectedOrder(order); }}
                      className="text-xs text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                    >
                      <Eye className="w-3 h-3" />
                      View
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); toast.info(`Printing invoice for ${order.orderNumber}`); }}
                      className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Printer className="w-3 h-3" />
                      Print
                    </button>
                    {order.paymentStatus !== 'PAID' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); toast.info('Payment collection modal coming soon'); }}
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
        )}
      </div>

      {/* Order Detail Modal - Placeholder */}
      {selectedOrder && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">
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

                <div className="flex gap-2 pt-4">
                  <button
                    onClick={() => toast.info(`Printing invoice for ${selectedOrder.orderNumber}`)}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    <Printer className="w-4 h-4" />
                    Print Invoice
                  </button>
                  {selectedOrder.balanceDue > 0 && (
                    <button
                      onClick={() => toast.info('Payment collection modal coming soon')}
                      className="btn-outline flex-1 flex items-center justify-center gap-2"
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
    </div>
  );
}

export default OrdersPage;
