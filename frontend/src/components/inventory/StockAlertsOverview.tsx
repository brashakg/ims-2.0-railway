// ============================================================================
// IMS 2.0 - Stock Alerts Overview
// ============================================================================
// Real-time inventory alerts: dead stock, low stock, reorder points, fast-moving

import { useState, useEffect } from 'react';
import {
  AlertTriangle,
  TrendingDown,
  TrendingUp,
  Truck,
  Zap,
  Package,
  RefreshCw,
  Loader2,
  Filter,
  Clock,
  Target,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

interface StockAlert {
  id: string;
  sku: string;
  productName: string;
  brand: string;
  category: string;
  currentStock: number;
  reorderPoint: number;
  safetyStock: number;
  projectedDaysToStockout: number;
  alertType: 'DEAD_STOCK' | 'LOW_STOCK' | 'REORDER_ALERT' | 'FAST_MOVING' | 'OVERSTOCK';
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  actionRequired: string;
  lastMovementDate?: string;
  daysWithoutMovement?: number;
  salesVelocity?: number; // units per day
  recommendedOrder?: number;
  costImpact?: number;
}

interface AlertStats {
  totalAlerts: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  deadStockValue: number;
  recommendedRestockValue: number;
}

type AlertFilter = 'all' | 'DEAD_STOCK' | 'LOW_STOCK' | 'REORDER_ALERT' | 'FAST_MOVING' | 'OVERSTOCK';
type SeverityFilter = 'all' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

export function StockAlertsOverview() {
  const { user } = useAuth();
  const [alerts, setAlerts] = useState<StockAlert[]>([]);
  const [stats, setStats] = useState<AlertStats>({
    totalAlerts: 0,
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    deadStockValue: 0,
    recommendedRestockValue: 0,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [alertTypeFilter, setAlertTypeFilter] = useState<AlertFilter>('all');
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadAlerts();
  }, [user?.activeStoreId]);

  const loadAlerts = async () => {
    setIsLoading(true);
    try {
      // Mock data - in production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 800));

      const mockAlerts: StockAlert[] = [
        {
          id: '1',
          sku: 'SG-078',
          productName: 'Vogue Cat Eye Sunglasses',
          brand: 'Vogue',
          category: 'Sunglasses',
          currentStock: 15,
          reorderPoint: 5,
          safetyStock: 10,
          projectedDaysToStockout: 180,
          alertType: 'DEAD_STOCK',
          severity: 'CRITICAL',
          actionRequired: 'Consider markdown or clearance sale',
          lastMovementDate: '2024-11-10',
          daysWithoutMovement: 91,
          costImpact: 22500,
          recommendedOrder: 0,
        },
        {
          id: '2',
          sku: 'FR-112',
          productName: 'Prada Baroque Frame',
          brand: 'Prada',
          category: 'Frames',
          currentStock: 3,
          reorderPoint: 5,
          safetyStock: 8,
          projectedDaysToStockout: 10,
          alertType: 'LOW_STOCK',
          severity: 'HIGH',
          actionRequired: 'Urgent reorder required',
          lastMovementDate: '2024-12-20',
          daysWithoutMovement: 51,
          salesVelocity: 0.3,
          recommendedOrder: 12,
          costImpact: 18000,
        },
        {
          id: '3',
          sku: 'FR-001',
          productName: 'Ray-Ban Aviator Classic',
          brand: 'Ray-Ban',
          category: 'Frames',
          currentStock: 5,
          reorderPoint: 8,
          safetyStock: 12,
          projectedDaysToStockout: 2,
          alertType: 'REORDER_ALERT',
          severity: 'CRITICAL',
          actionRequired: 'Place reorder immediately',
          lastMovementDate: '2025-02-06',
          salesVelocity: 2.5,
          recommendedOrder: 24,
          costImpact: 48000,
        },
        {
          id: '4',
          sku: 'CL-023',
          productName: 'Bausch & Lomb SofLens Daily',
          brand: 'Bausch & Lomb',
          category: 'Contact Lenses',
          currentStock: 120,
          reorderPoint: 50,
          safetyStock: 80,
          projectedDaysToStockout: 3,
          alertType: 'FAST_MOVING',
          severity: 'HIGH',
          actionRequired: 'High velocity item - maintain adequate stock',
          lastMovementDate: '2025-02-06',
          salesVelocity: 5.8,
          recommendedOrder: 60,
          costImpact: 60000,
        },
        {
          id: '5',
          sku: 'LS-156',
          productName: 'Crizal UV Protective Lens',
          brand: 'Essilor',
          category: 'Optical Lenses',
          currentStock: 200,
          reorderPoint: 100,
          safetyStock: 150,
          projectedDaysToStockout: 45,
          alertType: 'OVERSTOCK',
          severity: 'MEDIUM',
          actionRequired: 'Reduce procurement - excess inventory',
          lastMovementDate: '2025-02-05',
          salesVelocity: 4.4,
          recommendedOrder: 0,
          costImpact: 180000,
        },
        {
          id: '6',
          sku: 'ACC-045',
          productName: 'Lens Cleaning Kit Premium',
          brand: 'Opticare',
          category: 'Accessories',
          currentStock: 2,
          reorderPoint: 10,
          safetyStock: 15,
          projectedDaysToStockout: 0.25,
          alertType: 'LOW_STOCK',
          severity: 'CRITICAL',
          actionRequired: 'Reorder urgently - likely to stockout today',
          lastMovementDate: '2025-02-06',
          salesVelocity: 8,
          recommendedOrder: 30,
          costImpact: 7500,
        },
      ];

      // Calculate statistics
      const alertStats: AlertStats = {
        totalAlerts: mockAlerts.length,
        critical: mockAlerts.filter(a => a.severity === 'CRITICAL').length,
        high: mockAlerts.filter(a => a.severity === 'HIGH').length,
        medium: mockAlerts.filter(a => a.severity === 'MEDIUM').length,
        low: mockAlerts.filter(a => a.severity === 'LOW').length,
        deadStockValue: mockAlerts
          .filter(a => a.alertType === 'DEAD_STOCK')
          .reduce((sum, a) => sum + (a.costImpact || 0), 0),
        recommendedRestockValue: mockAlerts
          .filter(a => a.recommendedOrder && a.recommendedOrder > 0)
          .reduce((sum, a) => sum + ((a.recommendedOrder || 0) * (a.costImpact || 0) / a.currentStock), 0),
      };

      setAlerts(mockAlerts);
      setStats(alertStats);
    } catch (error) {
      console.error('Failed to load alerts:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const filteredAlerts = alerts.filter(alert => {
    const matchesType = alertTypeFilter === 'all' || alert.alertType === alertTypeFilter;
    const matchesSeverity = severityFilter === 'all' || alert.severity === severityFilter;
    const matchesSearch =
      alert.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
      alert.productName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      alert.brand.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesType && matchesSeverity && matchesSearch;
  });

  const getAlertIcon = (type: StockAlert['alertType']) => {
    switch (type) {
      case 'DEAD_STOCK':
        return <TrendingDown className="w-5 h-5" />;
      case 'LOW_STOCK':
        return <AlertTriangle className="w-5 h-5" />;
      case 'REORDER_ALERT':
        return <Truck className="w-5 h-5" />;
      case 'FAST_MOVING':
        return <TrendingUp className="w-5 h-5" />;
      case 'OVERSTOCK':
        return <Package className="w-5 h-5" />;
    }
  };

  const getAlertColor = (severity: StockAlert['severity']) => {
    switch (severity) {
      case 'CRITICAL':
        return 'bg-red-50 border-red-200 text-red-900';
      case 'HIGH':
        return 'bg-orange-50 border-orange-200 text-orange-900';
      case 'MEDIUM':
        return 'bg-yellow-50 border-yellow-200 text-yellow-900';
      case 'LOW':
        return 'bg-blue-50 border-blue-200 text-blue-900';
    }
  };

  const getSeverityBadgeColor = (severity: StockAlert['severity']) => {
    switch (severity) {
      case 'CRITICAL':
        return 'bg-red-100 text-red-800';
      case 'HIGH':
        return 'bg-orange-100 text-orange-800';
      case 'MEDIUM':
        return 'bg-yellow-100 text-yellow-800';
      case 'LOW':
        return 'bg-blue-100 text-blue-800';
    }
  };

  return (
    <div className="space-y-6">
      {/* Alert Statistics */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-red-700 uppercase tracking-wider">Critical</p>
          <p className="text-3xl font-bold text-red-900 mt-2">{stats.critical}</p>
        </div>
        <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-orange-700 uppercase tracking-wider">High</p>
          <p className="text-3xl font-bold text-orange-900 mt-2">{stats.high}</p>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-green-700 uppercase tracking-wider">Dead Stock Value</p>
          <p className="text-2xl font-bold text-green-900 mt-2">₹{(stats.deadStockValue / 1000).toFixed(0)}K</p>
        </div>
        <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-purple-700 uppercase tracking-wider">Restock Value</p>
          <p className="text-2xl font-bold text-purple-900 mt-2">₹{(stats.recommendedRestockValue / 1000).toFixed(0)}K</p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4">
        <div className="flex flex-col tablet:flex-row gap-4">
          {/* Search */}
          <div className="flex-1">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by SKU, product name, or brand..."
              className="w-full px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Alert Type Filter */}
          <select
            value={alertTypeFilter}
            onChange={(e) => setAlertTypeFilter(e.target.value as AlertFilter)}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="all">All Alert Types</option>
            <option value="DEAD_STOCK">Dead Stock (90+ days)</option>
            <option value="LOW_STOCK">Low Stock</option>
            <option value="REORDER_ALERT">Reorder Alert</option>
            <option value="FAST_MOVING">Fast Moving</option>
            <option value="OVERSTOCK">Overstock</option>
          </select>

          {/* Severity Filter */}
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value as SeverityFilter)}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="all">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>

          {/* Refresh */}
          <button
            onClick={loadAlerts}
            disabled={isLoading}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
          >
            <RefreshCw className={clsx('w-4 h-4', isLoading && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      {/* Alerts List */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
        </div>
      ) : filteredAlerts.length === 0 ? (
        <div className="bg-green-50 border border-green-200 rounded-lg p-8 text-center">
          <div className="flex justify-center mb-4">
            <Zap className="w-12 h-12 text-green-600" />
          </div>
          <h3 className="text-lg font-semibold text-green-900 mb-2">No Alerts</h3>
          <p className="text-green-700">Your inventory is in good shape! No critical or warning alerts.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredAlerts.map((alert) => (
            <div
              key={alert.id}
              className={clsx(
                'border rounded-lg p-4 space-y-3',
                getAlertColor(alert.severity)
              )}
            >
              <div className="flex items-start gap-3">
                <div className="flex-shrink-0 mt-1">{getAlertIcon(alert.alertType)}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="font-semibold">{alert.productName}</p>
                    <span className={clsx('px-2 py-1 rounded text-xs font-medium', getSeverityBadgeColor(alert.severity))}>
                      {alert.severity}
                    </span>
                  </div>
                  <p className="text-sm opacity-75 mb-2">{alert.brand} • {alert.sku}</p>
                  <p className="text-sm font-medium mb-2">{alert.actionRequired}</p>

                  <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 text-sm mb-2">
                    <div>
                      <p className="opacity-75 text-xs">Current Stock</p>
                      <p className="font-semibold">{alert.currentStock} units</p>
                    </div>
                    <div>
                      <p className="opacity-75 text-xs">Reorder Point</p>
                      <p className="font-semibold">{alert.reorderPoint} units</p>
                    </div>
                    {alert.daysWithoutMovement !== undefined && (
                      <div>
                        <p className="opacity-75 text-xs flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          Days Without Movement
                        </p>
                        <p className="font-semibold">{alert.daysWithoutMovement} days</p>
                      </div>
                    )}
                    {alert.recommendedOrder && alert.recommendedOrder > 0 && (
                      <div>
                        <p className="opacity-75 text-xs flex items-center gap-1">
                          <Target className="w-3 h-3" />
                          Recommended Order
                        </p>
                        <p className="font-semibold">{alert.recommendedOrder} units</p>
                      </div>
                    )}
                  </div>

                  {alert.costImpact && (
                    <div className="text-sm opacity-75">
                      <span className="font-medium">Financial Impact: </span>
                      ₹{alert.costImpact.toLocaleString('en-IN')}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default StockAlertsOverview;
