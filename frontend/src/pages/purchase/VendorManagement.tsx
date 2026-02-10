// ============================================================================
// IMS 2.0 - Vendor Management
// ============================================================================
// Vendor directory, scorecards, comparison, performance analytics, contracts

import { useState } from 'react';
import { Star, Phone, Mail, FileText, BarChart3, Filter, Plus } from 'lucide-react';
import clsx from 'clsx';

interface VendorPerformance {
  delivery_reliability: number;
  quality_rating: number;
  price_competitiveness: number;
  payment_terms: string;
  on_time_delivery_percentage: number;
  defect_rate: number;
  average_lead_days: number;
  total_pos: number;
  total_orders_received: number;
}

interface Vendor {
  id: string;
  name: string;
  contact: string;
  phone: string;
  address: string;
  performance: VendorPerformance;
  contract_status: 'active' | 'expiring_soon' | 'expired';
  contract_expiry: string;
}

const MOCK_VENDORS: Vendor[] = [
  {
    id: 'v-001',
    name: 'Optical Frames Ltd',
    contact: 'contact@frames.com',
    phone: '+91-1234567890',
    address: '123 Frame Street, Delhi',
    performance: {
      delivery_reliability: 4.5,
      quality_rating: 4.3,
      price_competitiveness: 4.0,
      payment_terms: 'Net 30',
      on_time_delivery_percentage: 92,
      defect_rate: 2.5,
      average_lead_days: 7,
      total_pos: 24,
      total_orders_received: 23,
    },
    contract_status: 'active',
    contract_expiry: '2024-12-31',
  },
  {
    id: 'v-002',
    name: 'Lens Manufacturers Inc',
    contact: 'sales@lensmakers.com',
    phone: '+91-9876543210',
    address: '456 Lens Road, Bangalore',
    performance: {
      delivery_reliability: 4.8,
      quality_rating: 4.7,
      price_competitiveness: 4.2,
      payment_terms: 'Net 45',
      on_time_delivery_percentage: 96,
      defect_rate: 1.2,
      average_lead_days: 10,
      total_pos: 18,
      total_orders_received: 18,
    },
    contract_status: 'active',
    contract_expiry: '2025-06-30',
  },
  {
    id: 'v-003',
    name: 'Accessories Wholesale',
    contact: 'bulk@accessories.com',
    phone: '+91-5555555555',
    address: '789 Accessories Ave, Mumbai',
    performance: {
      delivery_reliability: 3.8,
      quality_rating: 3.9,
      price_competitiveness: 4.5,
      payment_terms: 'Net 15',
      on_time_delivery_percentage: 85,
      defect_rate: 4.2,
      average_lead_days: 5,
      total_pos: 15,
      total_orders_received: 13,
    },
    contract_status: 'expiring_soon',
    contract_expiry: '2024-03-15',
  },
];

const getRatingColor = (rating: number) => {
  if (rating >= 4.5) return 'text-green-400';
  if (rating >= 3.5) return 'text-yellow-400';
  return 'text-orange-400';
};

const getContractStatusColor = (status: string) => {
  switch (status) {
    case 'active':
      return 'bg-green-900 text-green-300';
    case 'expiring_soon':
      return 'bg-yellow-900 text-yellow-300';
    case 'expired':
      return 'bg-red-900 text-red-300';
    default:
      return 'bg-gray-700 text-gray-300';
  }
};

export function VendorManagement() {
  const [activeTab, setActiveTab] = useState<'directory' | 'performance' | 'contracts'>('directory');
  const [filterRating, setFilterRating] = useState('all');

  const filteredVendors = MOCK_VENDORS.filter((vendor) => {
    if (filterRating === 'all') return true;
    if (filterRating === 'excellent') return vendor.performance.delivery_reliability >= 4.5;
    if (filterRating === 'good') return vendor.performance.delivery_reliability >= 3.5 && vendor.performance.delivery_reliability < 4.5;
    return vendor.performance.delivery_reliability < 3.5;
  });

  const avgDeliveryReliability = (MOCK_VENDORS.reduce((sum, v) => sum + v.performance.delivery_reliability, 0) / MOCK_VENDORS.length).toFixed(1);
  const avgQualityRating = (MOCK_VENDORS.reduce((sum, v) => sum + v.performance.quality_rating, 0) / MOCK_VENDORS.length).toFixed(1);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Vendor Management</h1>
          <p className="text-gray-400">Vendor directory and performance analytics</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
          <Plus className="w-5 h-5" />
          Add Vendor
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Vendors</p>
          <p className="text-2xl font-bold text-white">{MOCK_VENDORS.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Avg Delivery Reliability</p>
          <p className="text-2xl font-bold text-blue-400">{avgDeliveryReliability}/5.0</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Avg Quality Rating</p>
          <p className="text-2xl font-bold text-green-400">{avgQualityRating}/5.0</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Active Contracts</p>
          <p className="text-2xl font-bold text-purple-400">
            {MOCK_VENDORS.filter(v => v.contract_status === 'active').length}/{MOCK_VENDORS.length}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['directory', 'performance', 'contracts'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'directory' ? 'Directory' : tab === 'performance' ? 'Performance' : 'Contracts'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'directory' && (
        <div className="space-y-4">
          {/* Filter */}
          <div className="flex items-center gap-3">
            <Filter className="w-5 h-5 text-gray-400" />
            <select
              value={filterRating}
              onChange={(e) => setFilterRating(e.target.value)}
              className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-sm"
            >
              <option value="all">All Vendors</option>
              <option value="excellent">Excellent (4.5+)</option>
              <option value="good">Good (3.5-4.5)</option>
              <option value="fair">Fair (Below 3.5)</option>
            </select>
          </div>

          {/* Vendor Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredVendors.map((vendor) => (
              <div key={vendor.id} className="bg-gray-800 rounded-lg p-6 border border-gray-700 hover:border-gray-600 transition-colors">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex-1">
                    <h3 className="text-lg font-bold text-white mb-1">{vendor.name}</h3>
                    <p className="text-gray-400 text-sm">{vendor.address}</p>
                  </div>
                  <span className={clsx('px-2 py-1 rounded text-xs font-semibold', getContractStatusColor(vendor.contract_status))}>
                    {vendor.contract_status === 'active' ? 'Active' : vendor.contract_status === 'expiring_soon' ? 'Expiring Soon' : 'Expired'}
                  </span>
                </div>

                <div className="flex items-center gap-4 mb-4 pb-4 border-b border-gray-700">
                  <div className="flex items-center gap-1">
                    <Phone className="w-4 h-4 text-gray-400" />
                    <a href={`tel:${vendor.phone}`} className="text-gray-400 hover:text-gray-300 text-sm">{vendor.phone}</a>
                  </div>
                  <div className="flex items-center gap-1">
                    <Mail className="w-4 h-4 text-gray-400" />
                    <a href={`mailto:${vendor.contact}`} className="text-gray-400 hover:text-gray-300 text-sm truncate">{vendor.contact}</a>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div className="bg-gray-700 rounded p-3">
                    <p className="text-gray-400 text-xs mb-1">Delivery Reliability</p>
                    <div className="flex items-center gap-2">
                      <span className={clsx('text-lg font-bold', getRatingColor(vendor.performance.delivery_reliability))}>
                        {vendor.performance.delivery_reliability.toFixed(1)}
                      </span>
                      <Star className={clsx('w-4 h-4', getRatingColor(vendor.performance.delivery_reliability))} />
                    </div>
                  </div>
                  <div className="bg-gray-700 rounded p-3">
                    <p className="text-gray-400 text-xs mb-1">Quality Rating</p>
                    <div className="flex items-center gap-2">
                      <span className={clsx('text-lg font-bold', getRatingColor(vendor.performance.quality_rating))}>
                        {vendor.performance.quality_rating.toFixed(1)}
                      </span>
                      <Star className={clsx('w-4 h-4', getRatingColor(vendor.performance.quality_rating))} />
                    </div>
                  </div>
                </div>

                <div className="flex gap-2">
                  <button className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center justify-center gap-1">
                    <BarChart3 className="w-4 h-4" />
                    View Performance
                  </button>
                  <button className="flex-1 px-3 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-semibold flex items-center justify-center gap-1">
                    <FileText className="w-4 h-4" />
                    Edit
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'performance' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {filteredVendors.map((vendor) => (
            <div key={vendor.id} className="bg-gray-800 rounded-lg p-6 border border-gray-700">
              <h3 className="text-lg font-bold text-white mb-4">{vendor.name}</h3>

              <div className="space-y-3">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-400 text-sm">Delivery Reliability</span>
                    <span className="text-white font-semibold">{vendor.performance.delivery_reliability.toFixed(1)}/5.0</span>
                  </div>
                  <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-blue-500 h-full"
                      style={{ width: `${(vendor.performance.delivery_reliability / 5) * 100}%` }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-400 text-sm">Quality Rating</span>
                    <span className="text-white font-semibold">{vendor.performance.quality_rating.toFixed(1)}/5.0</span>
                  </div>
                  <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-green-500 h-full"
                      style={{ width: `${(vendor.performance.quality_rating / 5) * 100}%` }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-400 text-sm">Price Competitiveness</span>
                    <span className="text-white font-semibold">{vendor.performance.price_competitiveness.toFixed(1)}/5.0</span>
                  </div>
                  <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-yellow-500 h-full"
                      style={{ width: `${(vendor.performance.price_competitiveness / 5) * 100}%` }}
                    />
                  </div>
                </div>

                <div className="pt-3 border-t border-gray-700 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">On-Time Delivery</span>
                    <span className="text-green-400 font-semibold">{vendor.performance.on_time_delivery_percentage}%</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">Defect Rate</span>
                    <span className="text-orange-400 font-semibold">{vendor.performance.defect_rate}%</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">Avg Lead Days</span>
                    <span className="text-blue-400 font-semibold">{vendor.performance.average_lead_days} days</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">Total POs</span>
                    <span className="text-white font-semibold">{vendor.performance.total_pos}</span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'contracts' && (
        <div className="space-y-4">
          {MOCK_VENDORS.map((vendor) => (
            <div key={vendor.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700 flex items-center justify-between">
              <div className="flex-1">
                <h3 className="text-white font-semibold">{vendor.name}</h3>
                <p className="text-gray-400 text-sm">Expires: {new Date(vendor.contract_expiry).toLocaleDateString()}</p>
              </div>
              <div className="flex items-center gap-3">
                <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', getContractStatusColor(vendor.contract_status))}>
                  {vendor.contract_status === 'active' ? 'Active' : vendor.contract_status === 'expiring_soon' ? 'Expiring Soon' : 'Expired'}
                </span>
                <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center gap-2">
                  <FileText className="w-4 h-4" />
                  Renew
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default VendorManagement;
