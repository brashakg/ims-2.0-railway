// ============================================================================
// IMS 2.0 - Reorder Point Configuration Modal
// ============================================================================
// Set and manage reorder points for products

import { useState, useEffect } from 'react';
import {
  X,
  TrendingDown,
  AlertTriangle,
  Save,
  Loader2,
  BarChart3,
  Calculator,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

interface ReorderPointModalProps {
  isOpen: boolean;
  onClose: () => void;
  product: {
    id: string;
    sku: string;
    name: string;
    brand: string;
    currentStock: number;
    reorderPoint?: number;
    reorderQuantity?: number;
    maxStock?: number;
    averageSalesPerDay?: number;
    leadTimeDays?: number;
  };
  onSave: (data: ReorderPointData) => Promise<void>;
}

export interface ReorderPointData {
  productId: string;
  reorderPoint: number;
  reorderQuantity: number;
  maxStock: number;
  leadTimeDays: number;
}

export function ReorderPointModal({ isOpen, onClose, product, onSave }: ReorderPointModalProps) {
  const toast = useToast();

  const [isSaving, setIsSaving] = useState(false);
  const [reorderPoint, setReorderPoint] = useState(product.reorderPoint || 10);
  const [reorderQuantity, setReorderQuantity] = useState(product.reorderQuantity || 50);
  const [maxStock, setMaxStock] = useState(product.maxStock || 100);
  const [leadTimeDays, setLeadTimeDays] = useState(product.leadTimeDays || 7);
  const [autoCalculate, setAutoCalculate] = useState(false);

  // Sales velocity calculation
  const avgSalesPerDay = product.averageSalesPerDay || 2;
  const safetyStock = Math.ceil(avgSalesPerDay * 3); // 3 days buffer
  const leadTimeStock = Math.ceil(avgSalesPerDay * leadTimeDays);

  useEffect(() => {
    if (autoCalculate && avgSalesPerDay > 0) {
      // Reorder Point = (Average Daily Sales × Lead Time) + Safety Stock
      const calculatedReorderPoint = leadTimeStock + safetyStock;
      setReorderPoint(calculatedReorderPoint);

      // Reorder Quantity = Average Daily Sales × (Lead Time + Review Period)
      // Using 30-day review period
      const reviewPeriod = 30;
      const calculatedReorderQty = Math.ceil(avgSalesPerDay * (leadTimeDays + reviewPeriod));
      setReorderQuantity(calculatedReorderQty);

      // Max Stock = Reorder Point + Reorder Quantity
      const calculatedMaxStock = calculatedReorderPoint + calculatedReorderQty;
      setMaxStock(calculatedMaxStock);
    }
  }, [autoCalculate, avgSalesPerDay, leadTimeDays, leadTimeStock, safetyStock]);

  const handleSubmit = async () => {
    // Validation
    if (reorderPoint <= 0) {
      toast.error('Reorder point must be greater than 0');
      return;
    }
    if (reorderQuantity <= 0) {
      toast.error('Reorder quantity must be greater than 0');
      return;
    }
    if (maxStock < reorderPoint) {
      toast.error('Max stock must be greater than or equal to reorder point');
      return;
    }
    if (leadTimeDays <= 0) {
      toast.error('Lead time must be greater than 0');
      return;
    }

    setIsSaving(true);
    try {
      await onSave({
        productId: product.id,
        reorderPoint,
        reorderQuantity,
        maxStock,
        leadTimeDays,
      });
      toast.success('Reorder point configured successfully');
      onClose();
    } catch (error: any) {
      toast.error(error?.message || 'Failed to save reorder point');
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  const stockStatus = product.currentStock <= reorderPoint ? 'critical' : product.currentStock <= reorderPoint * 1.5 ? 'warning' : 'healthy';

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-orange-100 rounded-lg">
              <TrendingDown className="w-6 h-6 text-orange-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Configure Reorder Point</h2>
              <p className="text-sm text-gray-500">{product.name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSaving}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto" style={{ maxHeight: 'calc(90vh - 200px)' }}>
          {/* Product Info */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-6">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-600 mb-1">SKU</p>
                <p className="font-medium text-gray-900">{product.sku}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Brand</p>
                <p className="font-medium text-gray-900">{product.brand}</p>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Current Stock</p>
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">{product.currentStock} units</p>
                  {stockStatus === 'critical' && (
                    <span className="px-2 py-1 bg-red-100 text-red-800 text-xs font-medium rounded-full">
                      Critical
                    </span>
                  )}
                  {stockStatus === 'warning' && (
                    <span className="px-2 py-1 bg-yellow-100 text-yellow-800 text-xs font-medium rounded-full">
                      Low
                    </span>
                  )}
                </div>
              </div>
              <div>
                <p className="text-sm text-gray-600 mb-1">Avg. Sales/Day</p>
                <p className="font-medium text-gray-900">{avgSalesPerDay.toFixed(1)} units</p>
              </div>
            </div>
          </div>

          {/* Auto-calculate Toggle */}
          <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={autoCalculate}
                onChange={(e) => setAutoCalculate(e.target.checked)}
                className="w-4 h-4 text-purple-600 rounded focus:ring-purple-500"
              />
              <div className="flex items-center gap-2">
                <Calculator className="w-5 h-5 text-blue-600" />
                <div>
                  <p className="font-medium text-blue-900">Auto-calculate optimal values</p>
                  <p className="text-sm text-blue-700">
                    Based on sales velocity and lead time
                  </p>
                </div>
              </div>
            </label>
          </div>

          {/* Configuration Fields */}
          <div className="space-y-6">
            {/* Lead Time */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Supplier Lead Time (Days) *
              </label>
              <input
                type="number"
                min="1"
                max="365"
                value={leadTimeDays}
                onChange={(e) => setLeadTimeDays(parseInt(e.target.value) || 1)}
                disabled={autoCalculate && isSaving}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                Time taken for supplier to deliver after ordering
              </p>
            </div>

            {/* Reorder Point */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Reorder Point (Units) *
              </label>
              <input
                type="number"
                min="1"
                value={reorderPoint}
                onChange={(e) => setReorderPoint(parseInt(e.target.value) || 1)}
                disabled={autoCalculate || isSaving}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                {autoCalculate ? (
                  <>Calculated: {leadTimeStock} (lead time stock) + {safetyStock} (safety stock)</>
                ) : (
                  'Alert will trigger when stock falls to this level'
                )}
              </p>
            </div>

            {/* Reorder Quantity */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Reorder Quantity (Units) *
              </label>
              <input
                type="number"
                min="1"
                value={reorderQuantity}
                onChange={(e) => setReorderQuantity(parseInt(e.target.value) || 1)}
                disabled={autoCalculate || isSaving}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                {autoCalculate ? (
                  <>Optimized for 30-day inventory cycle</>
                ) : (
                  'Quantity to order when reorder point is reached'
                )}
              </p>
            </div>

            {/* Max Stock */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Maximum Stock Level (Units) *
              </label>
              <input
                type="number"
                min={reorderPoint}
                value={maxStock}
                onChange={(e) => setMaxStock(parseInt(e.target.value) || maxStock)}
                disabled={autoCalculate || isSaving}
                className="input-field w-full"
              />
              <p className="text-xs text-gray-500 mt-1">
                {autoCalculate ? (
                  <>Prevents overstocking: Reorder Point + Reorder Quantity</>
                ) : (
                  'Maximum inventory level to maintain'
                )}
              </p>
            </div>
          </div>

          {/* Visual Indicator */}
          <div className="mt-6 p-4 bg-gradient-to-r from-purple-50 to-blue-50 border border-purple-200 rounded-lg">
            <div className="flex items-center gap-3 mb-3">
              <BarChart3 className="w-5 h-5 text-purple-600" />
              <p className="font-medium text-gray-900">Stock Level Visualization</p>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">Current Stock</span>
                <span className="font-medium">{product.currentStock} units</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-3">
                <div
                  className={`h-3 rounded-full transition-all ${
                    product.currentStock <= reorderPoint
                      ? 'bg-red-500'
                      : product.currentStock <= reorderPoint * 1.5
                      ? 'bg-yellow-500'
                      : 'bg-green-500'
                  }`}
                  style={{ width: `${Math.min((product.currentStock / maxStock) * 100, 100)}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>0</span>
                <span className="text-orange-600 font-medium">
                  Reorder: {reorderPoint}
                </span>
                <span>Max: {maxStock}</span>
              </div>
            </div>
          </div>

          {/* Info Box */}
          <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex gap-3">
              <AlertTriangle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-medium mb-1">Reorder Point Guidelines</p>
                <ul className="list-disc list-inside space-y-1 text-blue-800">
                  <li>Set reorder point above lead time demand to avoid stockouts</li>
                  <li>Include safety stock buffer for demand variability</li>
                  <li>Review and adjust based on seasonal trends</li>
                  <li>System will alert when stock reaches reorder point</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            disabled={isSaving}
            className="btn-outline"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving}
            className="btn-primary flex items-center gap-2"
          >
            {isSaving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                Save Configuration
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
