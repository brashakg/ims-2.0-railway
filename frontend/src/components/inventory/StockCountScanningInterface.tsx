// ============================================================================
// IMS 2.0 - Stock Count Scanning Interface
// ============================================================================
// The count sheet: scan (or type) a barcode, type what is physically on the
// shelf, and the quantity is RECORDED onto the open count session. Before the
// S2 fix this panel posted to the same endpoint without a session, so the
// server resolved the barcode, calculated a difference and threw it away --
// which is why every completed count reported a perfect result.
//
// THE COUNT IS BLIND (owner ruling 2026-08-25): the server withholds every
// expected quantity while the session is open, and this screen must not show
// one from any source -- no "books say" column, no variance, no
// green-on-match tell. Expected vs counted belongs to the variance review
// AFTER submission.

import { useState, useEffect, useCallback } from 'react';
import { Barcode, AlertCircle, CheckCircle, Loader2, ListChecks } from 'lucide-react';
import api, { inventoryApi } from '../../services/api';

interface Props {
  /** The in-progress count session every scan is written onto. Required:
   *  a scan with nowhere to go is the defect this panel used to be. */
  countId: string;
  /** Fired after a quantity is persisted, so the session header can refresh. */
  onRecorded?: (itemsCounted: number) => void;
}

/** One line of the count SHEET: something this session expects to find.
 *  `counted_quantity: null` means nobody has answered for it yet -- a counted
 *  ZERO is a real answer (the style has walked entirely) and must not read as
 *  "not counted".
 *
 *  THE COUNT IS BLIND (owner ruling 2026-08-25): while the session is open
 *  the server withholds every expected quantity, so there is no
 *  system_quantity here and this screen must never show "books say N", a
 *  variance, or a matched/unmatched tell. Expected vs counted appears in the
 *  variance review AFTER the count is submitted. */
interface ExpectedLine {
  product_id: string;
  product_name: string;
  sku: string;
  counted_quantity: number | null;
}

/** What a recording scan answers with. Deliberately NO system count and NO
 *  variance: the session is open, so the count is blind. */
interface ScanResult {
  barcode: string;
  product_id: string;
  product_name: string;
  sku: string;
  physical_count: number;
  notes?: string;
  count_id?: string;
  recorded?: boolean;
  items_counted?: number | null;
}

export function StockCountScanningInterface({ countId, onRecorded }: Props) {
  const [barcode, setBarcode] = useState('');
  const [physicalCount, setPhysicalCount] = useState('');
  const [notes, setNotes] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [scannedItems, setScannedItems] = useState<ScanResult[]>([]);
  const [sheet, setSheet] = useState<ExpectedLine[]>([]);
  const [sheetLoading, setSheetLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingLine, setSavingLine] = useState<string | null>(null);

  // The count sheet: what this session expects to find. Without it the only
  // way to answer for a style is to scan one of its units -- and if the last
  // one has walked, so has its label.
  const loadSheet = useCallback(async () => {
    setSheetLoading(true);
    try {
      const doc = await inventoryApi.getStockCount(countId);
      setSheet(doc?.expected_lines || []);
    } catch {
      setSheet([]);
    } finally {
      setSheetLoading(false);
    }
  }, [countId]);

  useEffect(() => {
    loadSheet();
  }, [loadSheet]);

  const saveLine = async (line: ExpectedLine) => {
    const raw = drafts[line.product_id];
    if (raw === undefined || raw === '') return;
    const qty = parseInt(raw, 10);
    if (Number.isNaN(qty) || qty < 0) {
      setError('Enter a whole number of units (0 if none are on the shelf).');
      return;
    }
    setSavingLine(line.product_id);
    setError('');
    try {
      const res = await inventoryApi.recordCountItem(countId, {
        product_id: line.product_id,
        product_name: line.product_name,
        sku: line.sku,
        counted_quantity: qty,
      });
      setSheet((prev) =>
        prev.map((l) => (l.product_id === line.product_id ? { ...l, counted_quantity: qty } : l))
      );
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[line.product_id];
        return next;
      });
      onRecorded?.(res?.items_counted ?? 0);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Could not record that quantity');
    } finally {
      setSavingLine(null);
    }
  };

  const countedLines = sheet.filter((l) => l.counted_quantity !== null).length;

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
        count_id: countId,
      });

      const scanResult: ScanResult = response.data;
      if (!scanResult.recorded) {
        // Never let the counter walk away believing a number was saved.
        setError('The count was NOT recorded. Reopen the session and scan again.');
        setResult(null);
        return;
      }
      setResult(scanResult);
      // A re-scan of the same product REPLACES its line on the server, so the
      // on-screen sheet mirrors that instead of showing the product twice.
      setScannedItems([
        ...scannedItems.filter((i) => i.product_id !== scanResult.product_id),
        scanResult,
      ]);
      onRecorded?.(scanResult.items_counted ?? 0);
      loadSheet();

      // Reset form
      setBarcode('');
      setPhysicalCount('');
      setNotes('');
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Barcode not found or error processing scan');
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

  return (
    <div className="space-y-6">
      {/* THE COUNT SHEET. Every line the session expects, with a box to write
          the answer in -- including a style whose last unit has walked, which
          has no barcode left to scan and is exactly what a count is for. */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div className="p-4 border-b border-gray-200 flex items-center gap-2">
          <ListChecks className="w-5 h-5 text-bv-red-500" />
          <h3 className="text-lg font-semibold text-gray-900">Count sheet</h3>
          <span className="text-sm text-gray-500">
            {sheetLoading ? 'loading…' : `${countedLines} of ${sheet.length} lines answered`}
          </span>
        </div>
        {sheetLoading ? (
          <div className="p-6 flex justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : sheet.length === 0 ? (
          <p className="p-4 text-sm text-gray-500">
            The books show no stock for this count's scope, so there is nothing to count.
          </p>
        ) : (
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="bg-white border-b border-gray-200 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">Product</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-500">On the shelf</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-500">&nbsp;</th>
                </tr>
              </thead>
              <tbody>
                {sheet.map((line) => (
                  <tr key={line.product_id} className="border-b border-gray-200">
                    <td className="px-3 py-2">
                      <p className="text-gray-900 text-xs font-medium">{line.product_name}</p>
                      <p className="text-gray-500 text-xs">{line.sku}</p>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <input
                        type="number"
                        min="0"
                        aria-label={`Counted quantity for ${line.product_name}`}
                        className="w-20 px-2 py-1 bg-gray-100 border border-gray-300 rounded text-right text-gray-900"
                        placeholder={line.counted_quantity === null ? '—' : String(line.counted_quantity)}
                        value={drafts[line.product_id] ?? ''}
                        onChange={(e) =>
                          setDrafts((prev) => ({ ...prev, [line.product_id]: e.target.value }))
                        }
                        onKeyPress={(e) => {
                          if (e.key === 'Enter') saveLine(line);
                        }}
                        onBlur={() => saveLine(line)}
                      />
                    </td>
                    <td className="px-3 py-2 text-right text-xs">
                      {savingLine === line.product_id ? (
                        <Loader2 className="w-4 h-4 animate-spin inline text-gray-400" />
                      ) : line.counted_quantity === null ? (
                        <span className="text-gray-400">not counted</span>
                      ) : (
                        <span className="text-green-600">counted {line.counted_quantity}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="px-4 py-3 text-xs text-gray-500 border-t border-gray-200">
          Write 0 for a style with nothing left on the shelf — that is the one a
          scanner can never find, because the last label left with the last frame.
        </p>
      </div>

      {/* Scan Form */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Barcode className="w-5 h-5 text-bv-red-500" />
          Count sheet - scan and record
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          Each scan writes the counted quantity onto this session. Scanning the
          same product again replaces its line.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Scan Barcode
            </label>
            <input
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Scan or enter barcode..."
              className="w-full px-3 py-2 bg-gray-100 border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:outline-none focus:border-bv-red-600"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Physical Count
            </label>
            <input
              type="number"
              value={physicalCount}
              onChange={(e) => setPhysicalCount(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="0"
              min="0"
              className="w-full px-3 py-2 bg-gray-100 border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:outline-none focus:border-bv-red-600"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Notes (Optional)
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add any notes about this scan..."
              rows={2}
              className="w-full px-3 py-2 bg-gray-100 border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:outline-none focus:border-bv-red-600"
            />
          </div>

          {error && (
            <div className="p-3 bg-red-50 border border-red-600 rounded text-red-700 text-sm flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}

          <button
            onClick={handleScan}
            disabled={loading}
            className="w-full px-4 py-2 bg-bv-red-600 text-white rounded font-medium hover:bg-bv-red-700 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Recording...' : 'Record counted quantity'}
          </button>
        </div>
      </div>

      {/* Last scan: a SAVED acknowledgement only. The count is blind, so this
          panel must never show a system count, a variance, or anything that
          reads differently on a match. */}
      {result && (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
          <div className="flex items-start justify-between mb-3">
            <h4 className="font-semibold text-gray-900">Recorded</h4>
            <CheckCircle className="w-5 h-5 text-gray-400" />
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Product:</span>
              <span className="text-gray-900 font-medium">{result.product_name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">SKU:</span>
              <span className="text-gray-700">{result.sku}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Counted:</span>
              <span className="text-gray-900 font-semibold">{result.physical_count}</span>
            </div>
          </div>
        </div>
      )}

      {/* Scanned Items Summary */}
      {scannedItems.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200">
            <h4 className="font-semibold text-gray-900">
              Recorded this session ({scannedItems.length})
            </h4>
          </div>
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="bg-white border-b border-gray-200 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">
                    Product
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-500">
                    Counted
                  </th>
                </tr>
              </thead>
              <tbody>
                {scannedItems.map((item, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-gray-200 hover:bg-white"
                  >
                    <td className="px-3 py-2">
                      <p className="text-gray-900 text-xs font-medium">{item.product_name}</p>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-900 font-semibold">
                      {item.physical_count}
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
