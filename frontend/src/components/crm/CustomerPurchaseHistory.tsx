// ============================================================================
// Customer Purchase History Summary Card (CRM - Customer 360 View)
// ============================================================================
// Shows: Total spend, # of orders, avg order value, favorite brand, last visit

import { useState, useEffect } from 'react';
import { ShoppingBag, Calendar, Loader2 } from 'lucide-react';
import { orderApi } from '../../services/api';
import {
  CUSTOMER_SPEND_LABEL,
  CUSTOMER_SPEND_NOTE,
  CUSTOMER_SPEND_CAPPED_NOTE,
  CUSTOMER_SPEND_ORDER_LIMIT,
  summariseCustomerSpend,
} from '../../utils/customerSpend';

interface PurchaseHistoryData {
  totalSpend: number;
  orderCount: number;
  avgOrderValue: number;
  favoriteBrand?: string;
  lastVisitDate?: string;
  capped?: boolean;
}

interface CustomerPurchaseHistoryProps {
  customerId: string;
}

export function CustomerPurchaseHistory({ customerId }: CustomerPurchaseHistoryProps) {
  const [data, setData] = useState<PurchaseHistoryData | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    loadPurchaseHistory();
  }, [customerId]);

  const loadPurchaseHistory = async () => {
    setIsLoading(true);
    try {
      // L2-CUST-F01: the same orders and the same spend rule as the page's
      // own cards and Customer 360 (utils/customerSpend).
      const orders = await orderApi.getOrders({ customerId, limit: CUSTOMER_SPEND_ORDER_LIMIT });
      const orderList = orders?.orders || orders || [];

      if (Array.isArray(orderList) && orderList.length > 0) {
        const spend = summariseCustomerSpend(orderList);

        // Find favorite brand
        const brandCounts: Record<string, number> = {};
        orderList.forEach((order: any) => {
          const items = order.items || [];
          items.forEach((item: any) => {
            if (item.brand) {
              brandCounts[item.brand] = (brandCounts[item.brand] || 0) + 1;
            }
          });
        });
        const favoriteBrand = Object.entries(brandCounts).sort((a, b) => b[1] - a[1])[0]?.[0];

        setData({
          totalSpend: spend.totalSpend,
          orderCount: spend.orderCount,
          avgOrderValue: spend.avgOrderValue,
          favoriteBrand,
          lastVisitDate: spend.lastOrderDate || undefined,
          capped: spend.capped,
        });
      } else {
        setData({
          totalSpend: 0,
          orderCount: 0,
          avgOrderValue: 0,
        });
      }
    } catch {
      setData(null);
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="card flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card bg-red-50 text-red-600">
        <p className="text-sm">Failed to load purchase history</p>
      </div>
    );
  }

  const monthsAgo = data.lastVisitDate 
    ? Math.floor((Date.now() - new Date(data.lastVisitDate).getTime()) / (1000 * 60 * 60 * 24 * 30))
    : null;

  return (
    <div className="card border-2 border-bv-red-200 bg-bv-red-50">
      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
        <ShoppingBag className="w-5 h-5 text-bv-red-600" />
        Purchase History
      </h3>

      <div className="grid grid-cols-2 gap-4 mb-4">
        {/* Total Spend */}
        <div className="bg-white rounded-lg p-3 border border-bv-red-100">
          <p className="text-xs text-gray-600 uppercase font-semibold">{CUSTOMER_SPEND_LABEL}</p>
          <p className="text-2xl font-bold text-bv-red-600">₹{data.totalSpend.toLocaleString('en-IN')}</p>
          <p className="text-[11px] text-gray-400">{CUSTOMER_SPEND_NOTE}</p>
        </div>

        {/* Order Count */}
        <div className="bg-white rounded-lg p-3 border border-bv-red-100">
          <p className="text-xs text-gray-600 uppercase font-semibold">Orders</p>
          <p className="text-2xl font-bold text-gray-900">{data.orderCount}</p>
        </div>

        {/* Avg Order Value */}
        <div className="bg-white rounded-lg p-3 border border-blue-100">
          <p className="text-xs text-gray-600 uppercase font-semibold">Avg Order Value</p>
          <p className="text-2xl font-bold text-blue-600">₹{data.avgOrderValue.toLocaleString('en-IN')}</p>
        </div>

        {/* Favorite Brand */}
        {data.favoriteBrand && (
          <div className="bg-white rounded-lg p-3 border border-green-100">
            <p className="text-xs text-gray-600 uppercase font-semibold">Favorite Brand</p>
            <p className="text-2xl font-bold text-green-600 truncate">{data.favoriteBrand}</p>
          </div>
        )}
      </div>

      {/* Last Visit */}
      {data.lastVisitDate && monthsAgo !== null && (
        <div className="bg-white rounded-lg p-3 border border-gray-200 flex items-center gap-2">
          <Calendar className="w-4 h-4 text-gray-600" />
          <div>
            <p className="text-xs text-gray-600">Last Visit</p>
            <p className="text-sm font-semibold text-gray-900">
              {monthsAgo === 0 ? 'This month' : `${monthsAgo} month${monthsAgo > 1 ? 's' : ''} ago`}
            </p>
          </div>
        </div>
      )}

      {data.capped && (
        <p className="text-xs text-gray-600 mt-2">{CUSTOMER_SPEND_CAPPED_NOTE}</p>
      )}

      {data.orderCount === 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
          No purchase history yet
        </div>
      )}
    </div>
  );
}
