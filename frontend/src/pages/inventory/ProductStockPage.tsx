// ============================================================================
// IMS 2.0 - A product's stock and its one average cost  (/inventory/stock/:productId)
// ============================================================================
// S4 (owner MCQ 2026-10-23, board "Cost price of a product"): ONE average
// cost per product. It changes only when the accountant accepts a vendor bill
// (or corrects it with a reason); every piece on the shelf carries it, so the
// page shows the COST HISTORY, never a per-piece cost. Stock value = pieces x
// average cost, the one rule every report uses (backend stock_value).
//
// What we pay is SUPERADMIN / ADMIN / ACCOUNTANT only (owner ruling
// 2026-10-12): everyone else sees the pieces and their labels. The server
// refuses the cost reads to them; this page only leaves the boxes out.

import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Loader2, Printer } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { COST_ROLES } from '../../components/common/CostCell';
import { UnitLabelsModal } from '../../components/labels/UnitLabelsModal';
import { inventoryApi, productApi } from '../../services/api';
import type { CostHistoryRow, ProductCostCard, StockUnit } from '../../services/api/inventory';
import { formatDateIST } from '../../utils/datetime';
import { getErrorMessage } from '../../utils/errorHandler';

const money = (n?: number | null) =>
  n == null ? '—' : `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;

const STATUS_TEXT: Record<string, string> = {
  AVAILABLE: 'In stock',
  RESERVED: 'Reserved · customer order',
};

/** One cost-history row in shop words, as the board writes it. */
export function historyLine(row: CostHistoryRow): { label: string; text: string } {
  const who = row.by_name || row.by || 'someone';
  const move = `${money(row.old_cost)} → ${money(row.new_cost)}`;
  if (row.kind === 'BILL') {
    const pcs = `${row.qty} piece${row.qty === 1 ? '' : 's'} at ${money(row.price)}`;
    const bill = row.bill_number ? `bill ${row.bill_number}` : 'a bill';
    const how = row.method === 'LATEST_PRICE_PAID' ? 'bought without an order (latest price paid)' : `average ${money(row.new_cost)}`;
    return { label: 'Bill', text: `${bill}${row.vendor_name ? ` (${row.vendor_name})` : ''} accepted by ${who}: ${pcs} → ${how}` };
  }
  if (row.kind === 'CORRECTION') {
    return { label: 'Correction', text: `${who}: average ${move} · reason "${row.reason || ''}"` };
  }
  if (row.kind === 'CARRIED_OVER') {
    return { label: 'Carried over', text: `average ${money(row.new_cost)} as it stood before bill-by-bill averaging began` };
  }
  return {
    label: row.kind === 'OPENING_STOCK' ? 'Opening stock' : 'First cost',
    text: `first average ${money(row.new_cost)}${row.kind === 'BACKFILL' ? ' from the pieces received before' : ''}`,
  };
}

export function ProductStockPage() {
  const { productId = '' } = useParams();
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const showCost = hasRole(COST_ROLES);
  const storeId = user?.activeStoreId;

  const [product, setProduct] = useState<Record<string, unknown> | null>(null);
  const [units, setUnits] = useState<StockUnit[] | null>(null);
  const [card, setCard] = useState<ProductCostCard | null>(null);
  const [costFailed, setCostFailed] = useState(false);
  const [failed, setFailed] = useState(false);
  const [labels, setLabels] = useState(false);
  const [newCost, setNewCost] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  const loadCost = useCallback(async () => {
    if (!showCost) return;
    setCostFailed(false);
    try {
      setCard(await inventoryApi.getProductCost(productId));
    } catch {
      // The cost card alone failed: the pieces still show (never "Product not found").
      setCostFailed(true);
    }
  }, [productId, showCost]);

  const load = useCallback(async () => {
    loadCost();
    try {
      const [doc, unitRes] = await Promise.all([
        productApi.getProduct(productId),
        storeId ? inventoryApi.getUnits({ store_id: storeId, product_id: productId }) : Promise.resolve({ units: [] as StockUnit[] }),
      ]);
      setProduct((doc as Record<string, unknown>) || {});
      setUnits((unitRes.units || []).filter((u) => u.in_shop));
    } catch {
      setFailed(true);
    }
  }, [productId, storeId, loadCost]);

  useEffect(() => {
    load();
  }, [load]);

  const saveCorrection = async () => {
    const value = Number(newCost);
    if (!(value > 0)) {
      toast.error('Type the new average cost (excl. GST).');
      return;
    }
    if (reason.trim().length < 3) {
      toast.error('Say why the cost is being corrected.');
      return;
    }
    setSaving(true);
    try {
      setCard(await inventoryApi.correctProductCost(productId, value, reason.trim()));
      setNewCost('');
      setReason('');
      toast.success('Average cost corrected. It is in the cost history.');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save the correction.'));
    } finally {
      setSaving(false);
    }
  };

  if (failed) {
    return (
      <div className="inv-body">
        <div className="card p-6 space-y-2">
          <h1 className="text-lg font-semibold">Product not found</h1>
          <Link to="/inventory/stock" className="text-sm">Back to stock</Link>
        </div>
      </div>
    );
  }
  if (!product || !units) {
    return (
      <div className="inv-body">
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-7 h-7 animate-spin" style={{ color: 'var(--bv)' }} />
        </div>
      </div>
    );
  }

  const name = String(product.name || product.model || productId);
  const sku = String(product.sku || '');

  return (
    <div className="inv-body space-y-4">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            <Link to="/inventory/stock">Stock</Link> · {sku || productId}
          </div>
          <h1>{name}</h1>
          {showCost && (
            <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
              One average cost for the product. It changes only when the accountant accepts a vendor bill; every
              piece on the shelf carries that same cost.
            </p>
          )}
        </div>
      </div>

      <div className="stat-strip">
        <div>
          <div className="l">Pieces in this shop</div>
          <div className="v">{units.length}</div>
        </div>
        {showCost && card && (
          <>
            <div><div className="l">Pieces in every shop</div><div className="v">{card.pieces}</div></div>
            <div><div className="l">Average cost (excl. GST)</div><div className="v">{money(card.average_cost)}</div></div>
            <div><div className="l">Stock value</div><div className="v">{money(card.stock_value)}</div></div>
          </>
        )}
      </div>
      {showCost && costFailed && (
        <p className="text-sm" style={{ color: 'var(--warn)' }}>
          The cost could not be loaded.{' '}
          <button type="button" className="btn" onClick={loadCost}>Retry</button>
        </p>
      )}
      {showCost && card && (
        <div className="space-y-1">
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
            Stock value = pieces in every shop × average cost ({card.pieces} × {money(card.average_cost)}). The same
            rule on every screen and report.
          </p>
          {card.uncosted_pieces > 0 && (
            <p className="text-sm" style={{ color: 'var(--warn)' }}>
              {card.uncosted_pieces} piece{card.uncosted_pieces === 1 ? ' has' : 's have'} no cost yet, so this figure
              is too low. Accepting the vendor bill (or a correction below) gives the product its cost.
            </p>
          )}
        </div>
      )}

      <div className="card p-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-semibold">Pieces in this shop</p>
          {units.length > 0 && (
            <button type="button" className="btn" onClick={() => setLabels(true)}>
              <Printer className="w-4 h-4" /> Labels
            </button>
          )}
        </div>
        {units.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>No piece of this product is in this shop.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Barcode</th>
                  <th>Shelf</th>
                  <th>Status</th>
                  <th>Label printed</th>
                </tr>
              </thead>
              <tbody>
                {units.map((u) => (
                  <tr key={u.stock_id}>
                    <td className="mono">{u.barcode}</td>
                    <td>{u.location_code || '—'}</td>
                    <td>{STATUS_TEXT[u.status] || u.status}</td>
                    <td>{u.barcode_printed ? 'sent to print' : 'not yet'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCost && card && (
        <div className="card p-4 space-y-2">
          <p className="font-semibold">Cost history</p>
          {card.history.length === 0 ? (
            <p className="text-sm" style={{ color: 'var(--ink-4)' }}>No bill has been accepted for this product yet.</p>
          ) : (
            <ul className="text-sm space-y-1">
              {card.history.map((row) => {
                const line = historyLine(row);
                return (
                  <li key={row.key}>
                    <span className="font-medium">{line.label}</span>{' '}
                    <span style={{ color: 'var(--ink-4)' }}>{formatDateIST(row.at)}</span> · {line.text}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Sales, transfers and counts never change the average.</p>
        </div>
      )}

      {showCost && card && (
        <div className="card p-4 space-y-3">
          <p className="font-semibold">Correct the average cost</p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm space-y-1">
              <span className="block">New average cost (excl. GST)</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                inputMode="decimal"
                className="input-field"
                value={newCost}
                onChange={(e) => setNewCost(e.target.value)}
              />
            </label>
            <label className="text-sm space-y-1 flex-1 min-w-[200px]">
              <span className="block">Reason (required)</span>
              <input
                type="text"
                className="input-field w-full"
                maxLength={300}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <button type="button" className="btn primary" disabled={saving} onClick={saveCorrection}>
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Save correction
            </button>
          </div>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
            Every correction is listed in the cost history with name and reason.
          </p>
        </div>
      )}

      {labels && (
        <UnitLabelsModal productId={productId} storeId={storeId} title={name} subtitle={sku} onClose={() => { setLabels(false); load(); }} />
      )}
    </div>
  );
}
