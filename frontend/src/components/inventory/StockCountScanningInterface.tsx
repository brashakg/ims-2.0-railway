// ============================================================================
// IMS 2.0 - Stock Count Scanning Interface
// ============================================================================
// Simple barcode scanner with physical count entry

import { useState } from 'react';
import { Barcode, AlertCircle, CheckCircle } from 'lucide-react';
import api from '../../services/api';
import clsx from 'clsx';

interface ScanResult {
  barcode: string;
  product_id: string;
  product_name: string;
  sku: string;
  system_count: number;
  physical_count: number;
  variance: number;
  variance_percent: number;
  notes?: string;
}

export function StockCountScanningInterface() {
  const [barcode, setBarcode] = useState('');
  const [physicalCount, setPhysicalCount] = useState('');
  const [notes, setNotes] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [scannedItems, setScannedItems] = useState<ScanResult[]>([]);

  const handleScan = async () => {
    if (!barcode.trim() || physicalCount === '') {
      setError('Please enter barcode and physical count');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const response = await api.post('/inventory/stock-count-scan', {
        barcode: barcode.trim(),
        physical_count: parseInt(physicalCount),
        notes: notes || undefined,
      });

      const scanResult = response.data;
      setResult(scanResult);
      setScannedItems([...scannedItems, scanResult]);

      // Reset form
      setBarcode('');
      setPhysicalCount('');
      setNotes('');
    } catch (err) {
      setError('Barcode not found or error processing scan');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleScan();
    }
  };

  const varianceColor = (variance: number) => {
    if (variance === 0) return 'text-green-400';
    if (variance > 0) return 'text-blue-400'; // More stock than system
    return 'text-red-400'; // Less stock than system
  };

  return (
    <div className="space-y-6">
      {/* Scan Form */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Barcode className="w-5 h-5 text-bv-gold-500" />
          Stock Count Scanner
        </h3>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Scan Barcode
            </label>
            <input
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Scan or enter barcode..."
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-bv-gold-500"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Physical Count
            </label>
            <input
              type="number"
              value={physicalCount}
              onChange={(e) => setPhysicalCount(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="0"
              min="0"
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-bv-gold-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Notes (Optional)
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add any notes about this scan..."
              rows={2}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-500 focus:outline-none focus:border-bv-gold-500"
            />
          </div>

          {error && (
            <div className="p-3 bg-red-900/30 border border-red-600 rounded text-red-300 text-sm flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}

          <button
            onClick={handleScan}
            disabled={loading}
            className="w-full px-4 py-2 bg-bv-gold-500 text-white rounded font-medium hover:bg-bv-gold-600 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Processing...' : 'Scan & Record'}
          </button>
        </div>
      </div>

      {/* Last Scan Result */}
      {result && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm p-4">
          <div className="flex items-start justify-between mb-3">
            <h4 className="font-semibold text-white">Last Scan Result</h4>
            {result.variance === 0 ? (
              <CheckCircle className="w-5 h-5 text-green-400" />
            ) : (
              <AlertCircle className="w-5 h-5 text-orange-400" />
            )}
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-400">Product:</span>
              <span className="text-white font-medium">{result.product_name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">SKU:</span>
              <span className="text-gray-300">{result.sku}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">System Count:</span>
              <span className="text-gray-300">{result.system_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Physical Count:</span>
              <span className="text-white font-semibold">{result.physical_count}</span>
            </div>
            <div className="flex justify-between pt-2 border-t border-gray-700">
              <span className="text-gray-400">Variance:</span>
              <span className={clsx('font-semibold', varianceColor(result.variance))}>
                {result.variance > 0 ? '+' : ''}
                {result.variance} ({result.variance_percent}%)
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Scanned Items Summary */}
      {scannedItems.length > 0 && (
        <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-700">
            <h4 className="font-semibold text-white">
              Scanned Items ({scannedItems.length})
            </h4>
          </div>
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 border-b border-gray-700 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-400">
                    Product
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-400">
                    System
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-400">
                    Physical
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-400">
                    Variance
                  </th>
                </tr>
              </thead>
              <tbody>
                {scannedItems.map((item, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-gray-700 hover:bg-gray-900"
                  >
                    <td className="px-3 py-2">
                      <p className="text-white text-xs font-medium">{item.product_name}</p>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-300">
                      {item.system_count}
                    </td>
                    <td className="px-3 py-2 text-right text-white font-semibold">
                      {item.physical_count}
                    </td>
                    <td className={clsx(
                      'px-3 py-2 text-right font-semibold',
                      varianceColor(item.variance)
                    )}>
                      {item.variance > 0 ? '+' : ''}{item.variance}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
