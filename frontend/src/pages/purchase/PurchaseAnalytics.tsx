// ============================================================================
// IMS 2.0 - Purchase Analytics Tab
// ============================================================================

import {
  ShoppingBag,
  DollarSign,
  Clock,
  Truck,
} from 'lucide-react';
import type { PurchaseOrder, Supplier } from './purchaseTypes';

interface PurchaseAnalyticsProps {
  purchaseOrders: PurchaseOrder[];
  suppliers: Supplier[];
}

export function PurchaseAnalytics({ purchaseOrders, suppliers }: PurchaseAnalyticsProps) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <ShoppingBag className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Total POs</p>
              <p className="text-2xl font-bold text-gray-900">{purchaseOrders.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <DollarSign className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Total Value</p>
              <p className="text-2xl font-bold text-gray-900">
                {'\u20B9'}{(purchaseOrders.reduce((sum, po) => sum + po.total, 0) / 100000).toFixed(1)}L
              </p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Truck className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Active Suppliers</p>
              <p className="text-2xl font-bold text-gray-900">{suppliers.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <Clock className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Pending Approval</p>
              <p className="text-2xl font-bold text-gray-900">
                {purchaseOrders.filter(po => po.status === 'PENDING').length}
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Supplier Performance Ranking</h3>
        <div className="space-y-3">
          {[...suppliers]
            .sort((a, b) => {
              const scoreA = (a.performance.onTimeDelivery + a.performance.qualityScore + a.performance.priceCompetitiveness) / 3;
              const scoreB = (b.performance.onTimeDelivery + b.performance.qualityScore + b.performance.priceCompetitiveness) / 3;
              return scoreB - scoreA;
            })
            .map((supplier, index) => {
              const avgScore = (supplier.performance.onTimeDelivery + supplier.performance.qualityScore + supplier.performance.priceCompetitiveness) / 3;
              return (
                <div key={supplier.id} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold ${
                    index === 0 ? 'bg-yellow-100 text-yellow-800' :
                    index === 1 ? 'bg-gray-200 text-gray-700' :
                    index === 2 ? 'bg-orange-100 text-orange-700' :
                    'bg-gray-100 text-gray-600'
                  }`}>
                    {index + 1}
                  </div>
                  <div className="flex-1">
                    <p className="font-medium text-gray-900">{supplier.name}</p>
                    <div className="flex items-center gap-4 mt-1">
                      <span className="text-xs text-gray-600">Delivery: {supplier.performance.onTimeDelivery}%</span>
                      <span className="text-xs text-gray-600">Quality: {supplier.performance.qualityScore}%</span>
                      <span className="text-xs text-gray-600">Price: {supplier.performance.priceCompetitiveness}%</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-gray-900">{avgScore.toFixed(1)}%</p>
                    <p className="text-xs text-gray-600">Overall Score</p>
                  </div>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
