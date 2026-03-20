// ============================================================================
// IMS 2.0 - Suppliers List Panel
// ============================================================================

import {
  Edit,
  User,
  Phone,
  Mail,
  MapPin,
  Truck,
} from 'lucide-react';
import type { Supplier } from './purchaseTypes';

interface SupplierPanelProps {
  suppliers: Supplier[];
}

export function SupplierPanel({ suppliers }: SupplierPanelProps) {
  return (
    <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
      {suppliers.map((supplier) => (
        <div key={supplier.id} className="card hover:shadow-lg transition-shadow">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h3 className="text-lg font-semibold text-gray-900">{supplier.name}</h3>
                <span className="px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded">
                  {supplier.code}
                </span>
              </div>
              <div className="flex items-center gap-1 mb-2">
                {[...Array(5)].map((_, i) => (
                  <svg
                    key={i}
                    className={`w-4 h-4 ${i < Math.floor(supplier.rating) ? 'text-yellow-400' : 'text-gray-300'}`}
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                  </svg>
                ))}
                <span className="text-sm text-gray-600 ml-2">{supplier.rating}/5</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                <Edit className="w-5 h-5 text-gray-600" />
              </button>
            </div>
          </div>

          <div className="space-y-2 mb-4">
            <div className="flex items-center gap-2 text-sm">
              <User className="w-4 h-4 text-gray-400" />
              <span className="text-gray-700">{supplier.contactPerson}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Phone className="w-4 h-4 text-gray-400" />
              <span className="text-gray-700">{supplier.phone}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Mail className="w-4 h-4 text-gray-400" />
              <span className="text-gray-700">{supplier.email}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <MapPin className="w-4 h-4 text-gray-400" />
              <span className="text-gray-700">{supplier.city}, {supplier.state}</span>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3 p-3 bg-gray-50 rounded-lg mb-3">
            <div>
              <p className="text-xs text-gray-600">On-Time Delivery</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.onTimeDelivery}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Quality Score</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.qualityScore}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Price Score</p>
              <p className="text-sm font-semibold text-gray-900">{supplier.performance.priceCompetitiveness}%</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-xs text-gray-600">Total Purchases</p>
              <p className="font-semibold text-gray-900">{'\u20B9'}{(supplier.totalPurchases / 100000).toFixed(1)}L</p>
            </div>
            <div>
              <p className="text-xs text-gray-600">Outstanding</p>
              <p className={`font-semibold ${supplier.currentOutstanding > supplier.creditLimit * 0.8 ? 'text-red-600' : 'text-gray-900'}`}>
                {'\u20B9'}{(supplier.currentOutstanding / 100000).toFixed(1)}L
              </p>
            </div>
          </div>
        </div>
      ))}

      {suppliers.length === 0 && (
        <div className="col-span-2 text-center py-12">
          <Truck className="w-12 h-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-500">No suppliers found</p>
        </div>
      )}
    </div>
  );
}
