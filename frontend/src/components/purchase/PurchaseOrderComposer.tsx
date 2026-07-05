// ============================================================================
// IMS 2.0 - Purchase Order Composer (procurement Phase 2C)
// ============================================================================
// ONE shared PO body used by BOTH entry points so the field set, labels, cost
// pre-fill, and validation are identical no matter which door the buyer came
// through (owner ruling: one PO form everywhere):
//   - the full-page manual form  (PurchaseOrderForm.tsx, mode="page")
//   - the Buy Desk quick draft   (BuyDeskDraftPOModal.tsx, mode="modal")
//
// The composer owns: vendor select, expected date, notes, the line-item table
// (product cell + qty + unit cost [with a "last paid Rs X on <date>" caption]
// + line total + remove), totals, validation, and submit. What DIFFERS per
// door -- how a line names its product (a searchable picker on the manual form
// vs a read-only name/sku on the Buy Desk) and whether the buyer may add fresh
// lines -- is injected via props, so neither door regresses.
//
// Validation (mirrors the manual-form rule AND the earlier Buy Desk fix, so we
// do not weaken either): every kept line needs product_id + quantity >= 1 +
// unit_cost > 0. Cost cannot be added later at receiving (accept_grn reads the
// PO's unit price), so a zero-cost line is blocked up front.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { FileText, Loader2, Plus, Trash2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { vendorsApi } from '../../services/api/inventory';

// A vendor as the composer needs it. Each caller maps its own source shape
// (purchase `Supplier` / vendors list) down to this before handing it over.
export interface ComposerVendorOption {
  id: string;
  name: string;
  code?: string;
}

// One editable PO line. `productId` is the REAL catalogued product_id; the PO
// catalog gate (ON) rejects any line without one.
export interface ComposerLine {
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  unitCost: number;
  taxRate: number;
  // Set true once the operator (or a caller default) has typed a cost -- guards
  // the last-cost prefill from ever overwriting a value someone chose.
  costTouched?: boolean;
  // Populated by the last-cost lookup so we can render the muted caption. Not
  // sent to the server.
  lastPaid?: { unitPrice: number; date?: string | null } | null;
}

// The payload the composer hands back on submit -- the caller wires this to
// vendorsApi.createPurchaseOrder (each door keeps its own success behaviour).
export interface ComposerSubmitPayload {
  vendorId: string;
  vendorName: string;
  expectedDate: string;
  notes: string;
  items: Array<{
    product_id: string;
    product_name: string;
    sku: string;
    quantity: number;
    unit_price: number;
    taxRate: number;
    lineTotal: number;
  }>;
  subtotal: number;
  taxAmount: number;
  grandTotal: number;
}

export interface PurchaseOrderComposerProps {
  mode: 'page' | 'modal';
  vendors: ComposerVendorOption[];
  vendorsLoading?: boolean;
  initialVendorId?: string;
  initialLines?: ComposerLine[];
  /** Note shown under the vendor picker (e.g. Buy Desk's one-vendor-per-draft
   *  hint). Omit for none. */
  vendorHint?: ReactNode;
  /** Render the product cell for a line. The manual form passes a searchable
   *  picker (which sets the line's product via the `pickProduct` helper, or
   *  clears it via `clearProduct`); the Buy Desk passes a read-only name/sku.
   *  The composer owns line state, so cell mutations flow back through these
   *  helpers rather than the caller reaching into composer internals. */
  renderProductCell: (args: {
    line: ComposerLine;
    index: number;
    pickProduct: (p: { productId: string; productName: string; sku: string; costPrice?: number }) => void;
    clearProduct: () => void;
  }) => ReactNode;
  /** Show the "Add Item" affordance so the buyer can append blank lines (manual
   *  form). Omit/false to lock the line set to what was passed (Buy Desk). */
  allowAddLine?: boolean;
  /** Optional post-append hook (rarely needed) fired after a blank line is added. */
  onAddLine?: () => void;
  /** Whether a line may be removed. Manual form: yes (min 1). Buy Desk: no. */
  allowRemoveLine?: boolean;
  onSubmit: (payload: ComposerSubmitPayload) => Promise<void>;
  submitLabel?: string;
  submittingLabel?: string;
  onCancel?: () => void;
}

// Helper the manual form's picker calls to fill a line's product identity while
// preserving any cost the buyer already typed. Exported so the picker cell can
// build the same shape.
export function applyPickedProduct(
  line: ComposerLine,
  picked: { productId: string; productName: string; sku: string; costPrice?: number },
): ComposerLine {
  const keepCost = line.costTouched || line.unitCost > 0;
  return {
    ...line,
    productId: picked.productId,
    productName: picked.productName,
    sku: picked.sku,
    // Seed from the catalog cost only when the line still has none; the buyer
    // can always override. This is NOT the last-paid prefill (that runs off the
    // vendor lookup) -- it's the catalog's own cost_price fallback.
    unitCost: keepCost ? line.unitCost : picked.costPrice && picked.costPrice > 0 ? picked.costPrice : 0,
    lastPaid: null,
  };
}

// ISO string -> "4 Jul 2026" (human-friendly, fail-soft to '' on garbage).
function formatPaidDate(raw?: string | null): string {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

const blankLine = (): ComposerLine => ({
  productId: '',
  productName: '',
  sku: '',
  quantity: 1,
  unitCost: 0,
  taxRate: 18,
  costTouched: false,
  lastPaid: null,
});

export function PurchaseOrderComposer({
  mode,
  vendors,
  vendorsLoading = false,
  initialVendorId = '',
  initialLines,
  vendorHint,
  renderProductCell,
  allowAddLine = false,
  onAddLine,
  allowRemoveLine = false,
  onSubmit,
  submitLabel = 'Create as Draft',
  submittingLabel = 'Creating...',
  onCancel,
}: PurchaseOrderComposerProps) {
  const toast = useToast();

  const [vendorId, setVendorId] = useState(initialVendorId);
  const [expectedDate, setExpectedDate] = useState('');
  const [notes, setNotes] = useState('');
  const [lines, setLines] = useState<ComposerLine[]>(
    initialLines && initialLines.length > 0 ? initialLines : [blankLine()],
  );
  const [saving, setSaving] = useState(false);
  // Synchronous re-entry guard: `disabled={saving}` only bites next render, so a
  // same-tick double-click could otherwise fire two POSTs.
  const submittingRef = useRef(false);

  // A caller can preselect a vendor asynchronously (Buy Desk resolves the single
  // preferred vendor only after the active vendor list loads). Adopt it -- but
  // only into an empty slot, never over a choice the operator already made.
  useEffect(() => {
    if (initialVendorId) setVendorId((prev) => prev || initialVendorId);
  }, [initialVendorId]);

  // --- exposed line mutators (the product cell reaches these via context-free
  // callbacks passed down through renderProductCell's closure in each caller) --
  const updateLine = useCallback(
    (index: number, patch: Partial<ComposerLine>) => {
      setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
    },
    [],
  );

  const removeLine = useCallback((index: number) => {
    setLines((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  }, []);

  const addLine = useCallback(() => {
    setLines((prev) => [...prev, blankLine()]);
    onAddLine?.();
  }, [onAddLine]);

  // ------------------------------------------------------------------------
  // COST PREFILL (Phase 2C). When a vendor is chosen AND lines carry products,
  // batch every product_id into ONE getLastCost call and, for each line whose
  // cost is still untouched/blank, fill it from the last agreed price and stash
  // the caption. Never overwrites a value the operator typed. Re-runs when the
  // vendor changes or a new product appears. Fail-soft: empty -> no caption.
  // ------------------------------------------------------------------------
  // Signature of "which products need a price under which vendor" -- lets us
  // debounce/guard so adding lines one at a time doesn't spam the endpoint and
  // we don't refetch when nothing relevant changed.
  const prefillKey = useMemo(() => {
    const pids = lines
      .filter((l) => l.productId && !l.costTouched && !(l.unitCost > 0))
      .map((l) => l.productId)
      .sort();
    return `${vendorId}::${pids.join(',')}`;
  }, [vendorId, lines]);

  const lastPrefillKey = useRef<string>('');

  useEffect(() => {
    if (!vendorId) return;
    if (prefillKey === lastPrefillKey.current) return;

    const productIds = lines
      .filter((l) => l.productId && !l.costTouched && !(l.unitCost > 0))
      .map((l) => l.productId);
    if (productIds.length === 0) {
      lastPrefillKey.current = prefillKey;
      return;
    }

    let cancelled = false;
    const t = setTimeout(async () => {
      const { costs } = await vendorsApi.getLastCost(vendorId, productIds);
      if (cancelled) return;
      lastPrefillKey.current = prefillKey;
      setLines((prev) =>
        prev.map((l) => {
          // Re-check the guard against CURRENT state: the operator may have
          // typed a cost while the request was in flight.
          if (!l.productId || l.costTouched || l.unitCost > 0) return l;
          const hit = costs[l.productId];
          if (!hit || !(hit.unit_price > 0)) return l;
          return { ...l, unitCost: hit.unit_price, lastPaid: { unitPrice: hit.unit_price, date: hit.date } };
        }),
      );
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [prefillKey, vendorId, lines]);

  // When the vendor changes, a cost we AUTO-prefilled from the previous vendor's
  // history no longer applies -- reset it to blank (and drop its caption) so the
  // new vendor's lookup repaints it. Operator-typed costs (costTouched) are left
  // exactly as chosen; we never overwrite a value someone entered.
  const prevVendorRef = useRef(vendorId);
  useEffect(() => {
    if (prevVendorRef.current === vendorId) return;
    prevVendorRef.current = vendorId;
    lastPrefillKey.current = '';
    setLines((prev) =>
      prev.map((l) =>
        l.lastPaid && !l.costTouched ? { ...l, unitCost: 0, lastPaid: null } : l,
      ),
    );
  }, [vendorId]);

  // ------------------------------------------------------------------------
  const lineTotal = (l: ComposerLine) => l.quantity * l.unitCost * (1 + l.taxRate / 100);
  const subtotal = useMemo(() => lines.reduce((s, l) => s + l.quantity * l.unitCost, 0), [lines]);
  const taxAmount = useMemo(
    () => lines.reduce((s, l) => s + (l.quantity * l.unitCost * l.taxRate) / 100, 0),
    [lines],
  );
  const grandTotal = subtotal + taxAmount;

  const handleSubmit = async () => {
    if (!vendorId) {
      toast.error('Please select a vendor');
      return;
    }
    // A valid line references a REAL catalogued product + positive qty + cost.
    const valid = lines.filter((l) => l.productId && l.quantity >= 1 && l.unitCost > 0);
    // A line with a product/qty/cost but NO picked product would be rejected by
    // the catalog gate -- surface it here instead of a raw 422.
    const unpicked = lines.filter((l) => !l.productId && (l.quantity > 0 || l.unitCost > 0));
    if (unpicked.length > 0) {
      toast.error('Pick a catalogued product for every line (or remove the empty line)');
      return;
    }
    // Cost is required up front (it can't be backfilled at receiving) -- mirrors
    // both the manual form and the Buy Desk fix. Checked BEFORE the generic
    // "add a line" guard so a lone zero-cost line gets the precise, actionable
    // message rather than a vague one.
    const zeroCost = lines.filter((l) => l.productId && l.quantity >= 1 && !(l.unitCost > 0));
    if (zeroCost.length > 0) {
      toast.error('Every line needs a unit cost above 0 — cost cannot be added later at receiving.');
      return;
    }
    if (valid.length === 0) {
      toast.error('Add at least one product with a quantity and unit cost');
      return;
    }

    if (submittingRef.current) return;
    submittingRef.current = true;
    setSaving(true);
    try {
      const vendor = vendors.find((v) => v.id === vendorId);
      await onSubmit({
        vendorId,
        vendorName: vendor?.name ?? 'Unknown',
        expectedDate,
        notes,
        items: valid.map((l) => ({
          product_id: l.productId,
          product_name: l.productName,
          sku: l.sku || 'N/A',
          quantity: l.quantity,
          unit_price: l.unitCost,
          taxRate: l.taxRate,
          lineTotal: lineTotal(l),
        })),
        subtotal,
        taxAmount,
        grandTotal,
      });
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to create purchase order';
      toast.error(msg);
    } finally {
      submittingRef.current = false;
      setSaving(false);
    }
  };

  const dense = mode === 'modal';

  return (
    <div className={dense ? 'space-y-5' : 'space-y-6'}>
      {/* Vendor + Expected date */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Vendor *</label>
          <select
            value={vendorId}
            onChange={(e) => setVendorId(e.target.value)}
            disabled={vendorsLoading}
            className="input-field"
            aria-label="Vendor"
          >
            <option value="">{vendorsLoading ? 'Loading vendors…' : 'Select a vendor…'}</option>
            {vendors.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
                {v.code ? ` (${v.code})` : ''}
              </option>
            ))}
          </select>
          {vendorHint ? <p className="mt-1 text-xs text-gray-400">{vendorHint}</p> : null}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Expected Delivery Date</label>
          <input
            type="date"
            value={expectedDate}
            onChange={(e) => setExpectedDate(e.target.value)}
            className="input-field"
            aria-label="Expected Delivery Date"
          />
        </div>
      </div>

      {/* Line Items */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <label className="block text-sm font-medium text-gray-700">Line Items *</label>
          {allowAddLine ? (
            <button
              type="button"
              onClick={addLine}
              className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
            >
              <Plus className="w-4 h-4" />
              Add Item
            </button>
          ) : null}
        </div>

        <div className={dense ? 'space-y-2 max-h-80 overflow-y-auto pr-1' : 'space-y-3'}>
          {lines.map((line, index) => {
            const paidDate = formatPaidDate(line.lastPaid?.date);
            return (
              <div key={index} className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                <div className="grid grid-cols-12 gap-2 items-start">
                  <div className="col-span-12 tablet:col-span-5">
                    <label className="block text-xs text-gray-600 mb-1">Product</label>
                    {renderProductCell({
                      line,
                      index,
                      pickProduct: (p) =>
                        setLines((prev) => prev.map((l, i) => (i === index ? applyPickedProduct(l, p) : l))),
                      clearProduct: () =>
                        updateLine(index, { productId: '', productName: '', sku: '', lastPaid: null }),
                    })}
                  </div>
                  <div className="col-span-4 tablet:col-span-1">
                    <label className="block text-xs text-gray-600 mb-1">Qty</label>
                    <input
                      type="number"
                      min="1"
                      value={line.quantity}
                      onChange={(e) => updateLine(index, { quantity: parseInt(e.target.value) || 0 })}
                      className="input-field text-sm"
                      aria-label={`Quantity for line ${index + 1}`}
                    />
                  </div>
                  <div className="col-span-8 tablet:col-span-3">
                    <label className="block text-xs text-gray-600 mb-1">Unit Cost ({'₹'})</label>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={line.unitCost}
                      onChange={(e) =>
                        updateLine(index, {
                          unitCost: parseFloat(e.target.value) || 0,
                          costTouched: true,
                        })
                      }
                      className="input-field text-sm"
                      aria-label={`Unit cost for line ${index + 1}`}
                    />
                    {line.lastPaid && paidDate ? (
                      <p className="mt-1 text-xs text-gray-400">
                        last paid {'₹'}
                        {line.lastPaid.unitPrice.toLocaleString('en-IN')} on {paidDate}
                      </p>
                    ) : null}
                  </div>
                  <div className="col-span-8 tablet:col-span-2 text-right">
                    <label className="block text-xs text-gray-600 mb-1">Total</label>
                    <p className="text-sm font-semibold text-gray-900 py-2">
                      {'₹'}
                      {lineTotal(line).toLocaleString('en-IN')}
                    </p>
                  </div>
                  <div className="col-span-4 tablet:col-span-1 flex justify-end">
                    {allowRemoveLine ? (
                      <button
                        type="button"
                        onClick={() => removeLine(index)}
                        disabled={lines.length === 1}
                        className="p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label={`Remove line ${index + 1}`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Totals */}
      <div className="flex justify-end">
        <div className="w-64 space-y-2 p-4 bg-gray-50 rounded-lg">
          <div className="flex justify-between text-sm">
            <span className="text-gray-600">Subtotal</span>
            <span className="font-medium text-gray-900">
              {'₹'}
              {subtotal.toLocaleString('en-IN')}
            </span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-gray-600">Tax</span>
            <span className="font-medium text-gray-900">
              {'₹'}
              {taxAmount.toLocaleString('en-IN')}
            </span>
          </div>
          <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
            <span className="text-gray-900">Grand Total</span>
            <span className="text-gray-900">
              {'₹'}
              {grandTotal.toLocaleString('en-IN')}
            </span>
          </div>
        </div>
      </div>

      {/* Notes */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Any additional notes for this purchase order..."
          rows={dense ? 2 : 3}
          className="input-field"
          aria-label="Notes"
        />
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-3 pt-2">
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
        ) : null}
        <button
          type="button"
          onClick={handleSubmit}
          disabled={saving}
          className="btn-primary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
          {saving ? submittingLabel : submitLabel}
        </button>
      </div>
    </div>
  );
}

export default PurchaseOrderComposer;
