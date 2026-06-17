// ============================================================================
// IMS 2.0 - Quarantine Queue (F21)
// ============================================================================
// The defective-unit holding pen. Lists every QUARANTINED stock unit for the
// store, flags how many still need a red "DO NOT SHELVE" label, and lets a
// manager mark a new unit, print a label, create an RTV, or lift a mistaken
// quarantine. Restrained light theme: neutral surface, red used only for the
// QUARANTINED semantic chip + the unlabeled-count badge.

import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Loader2,
  Printer,
  RefreshCw,
  RotateCcw,
  Tag,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  inventoryApi,
  type QuarantineUnit,
  type QuarantineLabel,
} from '../../services/api/inventory';

const QUARANTINE_REASONS = [
  'DEFECTIVE',
  'SCRATCHED',
  'CUSTOMER_RETURN_DAMAGED',
  'RECEIVED_DAMAGED',
  'QC_FAILED_WORKSHOP',
  'OTHER',
] as const;

// Open the browser print dialog with a minimal red label rendition. QZ Tray
// (silent raw printing) is wired separately; this is the always-available
// HTML fallback the rest of the label system already relies on.
function printLabel(label: QuarantineLabel): void {
  const w = window.open('', '_blank', 'width=420,height=320');
  if (!w) return;
  const safe = (s?: string) => (s || '').replace(/[<>&]/g, '');
  w.document.write(`
    <html><head><title>Quarantine label ${safe(label.stock_id)}</title></head>
    <body style="font-family:sans-serif;margin:0;padding:16px;">
      <div style="border:3px solid ${label.background_color || '#DC2626'};border-radius:8px;padding:16px;">
        <div style="background:${label.background_color || '#DC2626'};color:#fff;font-weight:700;padding:8px;text-align:center;border-radius:4px;">
          ${safe(label.header)}
        </div>
        <p style="margin:8px 0 0;font-weight:600;">${safe(label.name)} ${safe(label.brand)}</p>
        <p style="margin:4px 0;font-family:monospace;font-size:18px;">${safe(label.barcode_value)}</p>
        <p style="margin:4px 0;font-size:12px;">Reason: ${safe(label.quarantine_reason)}</p>
        <p style="margin:4px 0;font-size:12px;">Store: ${[safe(label.store_brand), safe(label.store_name), label.store_code ? '(' + safe(label.store_code) + ')' : ''].filter(Boolean).join(' ')} &middot; ${safe(label.quarantine_at)}</p>
        ${label.luxury_brand_line ? `<p style="margin:6px 0 0;font-weight:700;color:${label.background_color || '#DC2626'};">${safe(label.luxury_brand_line)}</p>` : ''}
      </div>
    </body></html>`);
  w.document.close();
  w.focus();
  w.print();
}

export function QuarantineQueue() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const canManage = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);

  const [items, setItems] = useState<QuarantineUnit[]>([]);
  const [unlabeled, setUnlabeled] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Mark-quarantine modal state.
  const [showMark, setShowMark] = useState(false);
  const [markStockId, setMarkStockId] = useState('');
  const [markReason, setMarkReason] = useState<string>(QUARANTINE_REASONS[0]);
  const [markNotes, setMarkNotes] = useState('');
  const [marking, setMarking] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await inventoryApi.getQuarantinedStock(
        user?.activeStoreId ? { store_id: user.activeStoreId } : undefined,
      );
      setItems(res.items || []);
      setUnlabeled(res.unlabeled_count || 0);
    } catch {
      toast.error('Could not load the quarantine queue');
    } finally {
      setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleMark = async () => {
    if (!markStockId.trim()) {
      toast.error('Enter the stock unit barcode / ID');
      return;
    }
    setMarking(true);
    try {
      await inventoryApi.quarantineStock(markStockId.trim(), {
        reason: markReason,
        notes: markNotes.trim() || undefined,
      });
      toast.success('Unit quarantined');
      setShowMark(false);
      setMarkStockId('');
      setMarkNotes('');
      setMarkReason(QUARANTINE_REASONS[0]);
      await load();
    } catch {
      toast.error('Could not quarantine that unit (check status / store)');
    } finally {
      setMarking(false);
    }
  };

  const handlePrint = async (stockId: string) => {
    setBusyId(stockId);
    try {
      const label = await inventoryApi.printQuarantineLabel(stockId);
      printLabel(label);
      await load();
    } catch {
      toast.error('Could not print the quarantine label');
    } finally {
      setBusyId(null);
    }
  };

  const handleLift = async (stockId: string) => {
    const reason = window.prompt('Reason for lifting this quarantine (min 5 chars):');
    if (!reason || reason.trim().length < 5) {
      if (reason !== null) toast.error('A lift reason of at least 5 characters is required');
      return;
    }
    setBusyId(stockId);
    try {
      await inventoryApi.liftQuarantine(stockId, reason.trim());
      toast.success('Quarantine lifted; unit restored to available');
      await load();
    } catch {
      toast.error('Could not lift the quarantine');
    } finally {
      setBusyId(null);
    }
  };

  const handleCreateRtv = (unit: QuarantineUnit) => {
    // Pre-fill the vendor-returns form with this defective unit's stock id; the
    // manager picks the vendor on that screen.
    navigate(`/purchase/vendor-returns?stock_id=${encodeURIComponent(unit.stock_id)}`);
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between flex-wrap gap-3 p-4 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-red-600" strokeWidth={1.8} />
          <h2 className="text-base font-semibold text-gray-900">Quarantine queue</h2>
          {unlabeled > 0 && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
              {unlabeled} unlabeled
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} disabled={isLoading} className="btn sm">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {canManage && (
            <button onClick={() => setShowMark(true)} className="btn sm primary">
              <Tag className="w-4 h-4" /> Mark quarantine
            </button>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-gray-500">No quarantined units</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Product</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Stock ID</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Reason</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Quarantined</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Labeled?</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">RTV vendor</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {items.map((u) => (
                <tr key={u.stock_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <p className="font-medium text-gray-900">{u.product_name || u.product_id}</p>
                    <p className="text-sm text-gray-500">{u.brand}</p>
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-700">{u.barcode || u.stock_id}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{u.quarantine_reason || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {u.quarantine_at ? String(u.quarantine_at).slice(0, 10) : '-'}
                    {u.quarantine_by_name ? <span className="block text-xs text-gray-400">{u.quarantine_by_name}</span> : null}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-red-600 text-white">
                      Quarantined
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    {u.quarantine_label_printed ? (
                      <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">Yes</span>
                    ) : (
                      <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">No</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    {u.rtv_vendor_id ? (
                      <span className="text-gray-700">{u.rtv_vendor_id}</span>
                    ) : (
                      <span className="text-gray-400">&mdash;</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-center gap-2">
                      {canManage && (
                        <button
                          onClick={() => handlePrint(u.stock_id)}
                          disabled={busyId === u.stock_id}
                          className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-bv-red-600"
                          title="Print quarantine label"
                        >
                          <Printer className="w-4 h-4" /> Print
                        </button>
                      )}
                      {canManage && !u.rtv_vendor_id && (
                        <button
                          onClick={() => handleCreateRtv(u)}
                          className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-bv-red-600"
                          title="Create vendor return for this unit"
                        >
                          <Tag className="w-4 h-4" /> Create RTV
                        </button>
                      )}
                      {canManage && (
                        <button
                          onClick={() => handleLift(u.stock_id)}
                          disabled={busyId === u.stock_id}
                          className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-bv-red-600"
                          title="Lift quarantine (mis-quarantine correction)"
                        >
                          <RotateCcw className="w-4 h-4" /> Lift
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Mark-quarantine modal */}
      {showMark && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md">
            <div className="p-4 border-b border-gray-200">
              <h3 className="text-base font-semibold text-gray-900">Mark unit quarantine</h3>
              <p className="text-sm text-gray-500">Pull a defective unit off the sellable floor.</p>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Stock unit barcode / ID</label>
                <input
                  className="input-field"
                  value={markStockId}
                  onChange={(e) => setMarkStockId(e.target.value)}
                  placeholder="Scan or type the unit barcode"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reason</label>
                <select className="input-field" value={markReason} onChange={(e) => setMarkReason(e.target.value)}>
                  {QUARANTINE_REASONS.map((r) => (
                    <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Notes <span className="text-gray-400">({markNotes.length}/200)</span>
                </label>
                <textarea
                  className="input-field"
                  rows={2}
                  maxLength={200}
                  value={markNotes}
                  onChange={(e) => setMarkNotes(e.target.value)}
                  placeholder="Optional"
                />
              </div>
            </div>
            <div className="p-4 border-t border-gray-200 flex justify-end gap-2">
              <button onClick={() => setShowMark(false)} className="btn sm" disabled={marking}>Cancel</button>
              <button onClick={handleMark} className="btn sm primary" disabled={marking}>
                {marking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Tag className="w-4 h-4" />}
                Mark quarantine
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
