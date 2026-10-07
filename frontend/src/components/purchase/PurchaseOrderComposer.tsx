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
import { AlertTriangle, FileText, Loader2, Plus, Trash2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { vendorsApi } from '../../services/api/inventory';

/** Today in the browser's own calendar, as the yyyy-mm-dd an <input type="date">
 *  understands. Used as the picker's floor -- the SERVER is what actually
 *  refuses a past date (a picker minimum is a courtesy, not a rule). */
export function todayForDateInput(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

// A vendor as the composer needs it. Each caller maps its own source shape
// (purchase `Supplier` / vendors list) down to this before handing it over.
export interface ComposerVendorOption {
  id: string;
  name: string;
  code?: string;
}

// The identity a buyer types for an item that is not in the catalogue yet
// (owner ruling 13). The server turns this into a real, provisional, unsellable
// product row and puts its id on the PO line.
export interface ComposerNewProduct {
  category: string;
  brand: string;
  model: string;
  colour: string;
  size: string;
  mrp: number;
}

// One editable PO line. It carries EITHER a real catalogued `productId` OR the
// typed-in identity of an item that does not exist yet -- never both.
export interface ComposerLine {
  productId: string;
  productName: string;
  sku: string;
  newProduct?: ComposerNewProduct | null;
  quantity: number;
  unitCost: number;
  /** GST percent for this line. Comes from the picked product's own catalogued
   *  rate (which was settled from its HSN), NOT from a flat house default --
   *  frames are 5% and sunglasses 18%, so one number for everything was simply
   *  wrong. 0 with gstResolved false means "we do not know yet", which the row
   *  says out loud instead of quietly charging something. */
  taxRate: number;
  /** The product's HSN, shown so a buyer can see WHY the rate is what it is. */
  hsn?: string | null;
  /** Colour / size / MRP -- what tells two frames of one model apart. */
  productDetail?: string;
  /** False when nothing on the product could settle a GST rate. */
  gstResolved?: boolean;
  /** WHY this line's GST is not settled, in the buyer's words -- and the ONE
   *  thing the amber flags key on, so the screen warns about exactly what the
   *  server warns about (its `gst_missing`). Non-empty even when the line IS
   *  taxed: a product with a catalogue rate but NO HSN is still a purchase
   *  document with no HSN on it, which is not allowed. */
  gstMissing?: string | null;
  // Set true once the operator (or a caller default) has typed a cost -- guards
  // the last-cost prefill from ever overwriting a value someone chose.
  costTouched?: boolean;
  /** The manager is in this cost box right now (set on focus, cleared on
   *  blur). A last-cost answer that lands meanwhile only captions the line:
   *  rewriting the box under the caret dropped the selection, so '2950'
   *  typed into a box that had just become 3100 read 31002950 (audit F67).
   *  Leaving it without typing puts that last-paid price in (takeLastPaid). */
  costFocused?: boolean;
  /** The picked product's own catalogue cost -- the seed the box falls back
   *  to when the chosen vendor has no price history for it. */
  catalogCost?: number;
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
    product_id?: string;
    product_name?: string;
    sku?: string;
    new_product?: ComposerNewProduct;
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
  /** Is this an inter-state purchase? true -> IGST, false -> CGST + SGST,
   *  null/undefined -> we cannot tell from the GST numbers we hold, so the
   *  totals say so rather than pretending. The caller works this out from the
   *  chosen vendor and the receiving shop, as the SERVER decides it (usePoGstHeads). */
  interstate?: boolean | null;
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
    pickProduct: (p: {
      productId: string;
      productName: string;
      sku: string;
      costPrice?: number;
      gstRate?: number | null;
      hsn?: string | null;
      detail?: string;
    }) => void;
    clearProduct: () => void;
    /** Attach (or clear, with null) the typed-in identity of an item that is
     *  not catalogued yet. */
    setNewProduct: (np: ComposerNewProduct | null) => void;
  }) => ReactNode;
  /** Show the "Add Item" affordance so the buyer can append blank lines (manual
   *  form). Omit/false to lock the line set to what was passed (Buy Desk). */
  allowAddLine?: boolean;
  /** Optional post-append hook (rarely needed) fired after a blank line is added. */
  onAddLine?: () => void;
  /** Told which vendor is selected, so the caller can work out the place of
   *  supply (and hand `interstate` back down). */
  onVendorChange?: (vendorId: string) => void;
  /** Whether a line may be removed. Manual form: yes (min 1). Buy Desk: no. */
  allowRemoveLine?: boolean;
  /** Told whether the operator has entered anything since the form opened,
   *  so the caller can ask before throwing it away (audit F87). */
  onDirtyChange?: (dirty: boolean) => void;
  onSubmit: (payload: ComposerSubmitPayload) => Promise<void>;
  submitLabel?: string;
  submittingLabel?: string;
  onCancel?: () => void;
}

/** What GST a product settles for a PO line: the rate to show, whether it is
 *  settled at all, and -- when it is not -- what is missing, in the buyer's
 *  words.
 *
 *  THE RATE COMES OFF THE PRODUCT, and nothing here works it out. The product's
 *  catalogued `gst_rate` IS the rate its HSN settles -- the cataloguing door
 *  derives it from the HSN server-side (product_master.normalise_payload ->
 *  gst_rates.resolve_gst_rate_strict), which is the same call, over the same
 *  owner-editable table, that the purchase side makes when it stores the line
 *  (vendors._po_line_gst_rate). So showing the catalogued rate here IS showing
 *  the rate the server will charge.
 *
 *  This screen deliberately keeps NO HSN -> rate table of its own. It used to,
 *  and the two tables drifted: the screen held 900319 at 5% while the server
 *  held no 900319 at all, and the server settled 852580 and every 4-digit
 *  heading that the screen was blind to. Two copies of a tax rule is how you
 *  get a purchase order that promises a vendor one number and books another.
 *  The HSN still travels with the line -- shown, and demanded, because a GST
 *  purchase document needs one -- it just does not price it here.
 *
 *  ONE rule for every door that opens a PO line -- the manual form's picker and
 *  the Buy Desk's bulk draft (which used to open every line at a flat 18%). */
export function gstForProduct(picked: { gstRate?: number | null; hsn?: string | null }): {
  taxRate: number;
  hsn: string | null;
  gstResolved: boolean;
  gstMissing: string | null;
} {
  const hsn = (picked.hsn ?? '').trim() || null;
  const rate =
    typeof picked.gstRate === 'number' && picked.gstRate >= 0 ? picked.gstRate : null;
  return {
    taxRate: rate ?? 0,
    hsn,
    gstResolved: rate !== null,
    gstMissing: !hsn ? 'no HSN on this product' : null,
  };
}

// Helper the manual form's picker calls to fill a line's product identity while
// preserving any cost the buyer already typed. Exported so the picker cell can
// build the same shape.
export function applyPickedProduct(
  line: ComposerLine,
  picked: {
    productId: string;
    productName: string;
    sku: string;
    costPrice?: number;
    gstRate?: number | null;
    hsn?: string | null;
    detail?: string;
  },
): ComposerLine {
  const catalogCost = picked.costPrice && picked.costPrice > 0 ? picked.costPrice : 0;
  const { taxRate, hsn, gstResolved, gstMissing } = gstForProduct(picked);
  return {
    ...line,
    productId: picked.productId,
    productName: picked.productName,
    sku: picked.sku,
    // Seed from the catalogue cost unless the buyer typed one. The seed is the
    // form's own guess, so the vendor's last-paid lookup still replaces it
    // (audit F22) -- and a cost auto-filled for the PREVIOUS product is not
    // carried over to this one.
    unitCost: line.costTouched ? line.unitCost : catalogCost,
    catalogCost,
    // The picked product carries its own GST rate + HSN. Nothing is guessed:
    // a product with neither leaves the line unresolved and visibly flagged.
    taxRate,
    hsn,
    productDetail: picked.detail ?? '',
    gstResolved,
    gstMissing,
    lastPaid: null,
  };
}

// The line's product is gone -- cleared ("Change product") or swapped for an
// item typed in because it is not catalogued. Everything that came WITH that
// product goes with it, including a cost the FORM filled for it (catalogue
// seed or last paid): left behind, it would be sent as the price of an item
// nobody priced. A cost the buyer typed stays -- it is theirs.
export function releaseProduct(line: ComposerLine, patch: Partial<ComposerLine> = {}): ComposerLine {
  return {
    ...line,
    productId: '',
    productName: '',
    sku: '',
    newProduct: null,
    taxRate: 0,
    hsn: null,
    productDetail: '',
    gstResolved: false,
    gstMissing: null,
    lastPaid: null,
    catalogCost: 0,
    unitCost: line.costTouched ? line.unitCost : 0,
    ...patch,
  };
}

// The ONE rule for when the form puts the last price paid to this vendor in
// the cost box (owner: it wins over the catalogue cost): there is one, the
// manager has not typed a cost, and they are not in the box right now. Run
// when the answer lands AND when they leave the box, so the box ends up the
// same whichever comes first -- and whatever the other lines do.
function takeLastPaid(l: ComposerLine): ComposerLine {
  return l.lastPaid && !l.costTouched && !l.costFocused
    ? { ...l, unitCost: l.lastPaid.unitPrice }
    : l;
}

// ISO string -> "4 Jul 2026" (human-friendly, fail-soft to '' on garbage).
function formatPaidDate(raw?: string | null): string {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

// What the operator has put on the form, as one comparable string.
function enteredSig(lines: ComposerLine[], expectedDate: string, notes: string): string {
  return JSON.stringify([
    expectedDate,
    notes.trim(),
    lines.map((l) => [l.productId, l.newProduct ?? null, l.quantity, l.costTouched ? l.unitCost : null]),
  ]);
}

const blankLine = (): ComposerLine => ({
  productId: '',
  productName: '',
  sku: '',
  newProduct: null,
  quantity: 1,
  unitCost: 0,
  // NOT 18. An empty line has no product yet, so it has no rate yet; picking
  // the product brings its own. The old flat 18% over-taxed every frame,
  // spectacle lens and contact lens on the page (all 5%).
  taxRate: 0,
  hsn: null,
  productDetail: '',
  gstResolved: false,
  // Nothing is wrong with an empty line yet -- it has no product to be wrong
  // about. The flag appears the moment a product without an HSN is picked.
  gstMissing: null,
  costTouched: false,
  lastPaid: null,
});

export function PurchaseOrderComposer({
  mode,
  vendors,
  interstate = null,
  vendorsLoading = false,
  initialVendorId = '',
  initialLines,
  vendorHint,
  renderProductCell,
  allowAddLine = false,
  onAddLine,
  onVendorChange,
  allowRemoveLine = false,
  onDirtyChange,
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

  // Audit F87: has the operator entered anything since the form opened? Only
  // what THEY put there counts -- a cost the form filled itself (catalogue /
  // last paid) does not, and a preselected vendor is where the form started.
  const [pristine] = useState(() => enteredSig(lines, expectedDate, notes));
  const dirty = vendorId !== initialVendorId || enteredSig(lines, expectedDate, notes) !== pristine;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  // A caller can preselect a vendor asynchronously (Buy Desk resolves the single
  // preferred vendor only after the active vendor list loads). Adopt it -- but
  // only into an empty slot, never over a choice the operator already made.
  useEffect(() => {
    if (initialVendorId) setVendorId((prev) => prev || initialVendorId);
  }, [initialVendorId]);

  // Hand the current vendor to the caller so it can resolve the place of
  // supply. One effect covers both the operator's pick and an async preselect.
  useEffect(() => {
    onVendorChange?.(vendorId);
  }, [vendorId, onVendorChange]);

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
  // batch every product_id into ONE getLastCost call. EVERY line gets the
  // caption with the last price agreed with THIS vendor -- a typed cost too,
  // so the buyer sees what they are bargaining against. Only a line whose
  // cost the operator has not typed has its box filled, over the catalogue
  // seed too (audit F22: the seed used to block the lookup, so it never ran).
  // Re-runs when the vendor changes or a new product appears. Fail-soft: no
  // history -> the seed stays, no caption.
  // ------------------------------------------------------------------------
  // Signature of "which products need a price under which vendor": the
  // lookup runs whenever it changes, debounced so adding lines one at a time
  // doesn't spam the endpoint. A string, so a Qty or cost keystroke (same
  // products) does not restart the debounce and hold the answer back until
  // the manager is already in the cost box. No memory of the last answered
  // set: a product cleared and picked back lost its price with the pick, so
  // coming back to an earlier set is asked again too.
  const prefillIds = useMemo(
    () =>
      lines
        .filter((l) => l.productId)
        .map((l) => l.productId)
        .sort()
        .join(','),
    [lines],
  );

  useEffect(() => {
    if (!vendorId || !prefillIds) return;
    const productIds = prefillIds.split(',');

    let cancelled = false;
    const t = setTimeout(async () => {
      let costs: Awaited<ReturnType<typeof vendorsApi.getLastCost>>['costs'];
      try {
        ({ costs } = await vendorsApi.getLastCost(vendorId, productIds));
      } catch {
        // The lookup failed: every line keeps the cost it has (the catalogue
        // seed) with no caption; the next product or vendor change asks
        // again. Never blocks the order.
        return;
      }
      if (cancelled) return;
      setLines((prev) =>
        prev.map((l) => {
          const hit = l.productId ? costs[l.productId] : undefined;
          if (!hit || !(hit.unit_price > 0)) return l;
          // Re-checked against CURRENT state: the operator may have typed a
          // cost -- or be in the box -- while the request was in flight.
          return takeLastPaid({ ...l, lastPaid: { unitPrice: hit.unit_price, date: hit.date } });
        }),
      );
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [prefillIds, vendorId]);

  // When the vendor changes, the previous vendor's price no longer applies:
  // every caption goes (a typed cost must not sit under another vendor's
  // price either), and a cost we AUTO-prefilled goes back to the catalogue
  // seed, so the new vendor's lookup repaints both. Operator-typed costs
  // (costTouched) are left exactly as chosen; we never overwrite a value
  // someone entered.
  const prevVendorRef = useRef(vendorId);
  useEffect(() => {
    if (prevVendorRef.current === vendorId) return;
    prevVendorRef.current = vendorId;
    setLines((prev) =>
      prev.map((l) =>
        !l.lastPaid
          ? l
          : l.costTouched
            ? { ...l, lastPaid: null }
            : { ...l, unitCost: l.catalogCost ?? 0, lastPaid: null },
      ),
    );
  }, [vendorId]);

  // ------------------------------------------------------------------------
  const lineTax = (l: ComposerLine) => (l.quantity * l.unitCost * l.taxRate) / 100;
  const lineTotal = (l: ComposerLine) => l.quantity * l.unitCost + lineTax(l);
  const subtotal = useMemo(() => lines.reduce((s, l) => s + l.quantity * l.unitCost, 0), [lines]);
  const taxAmount = useMemo(() => lines.reduce((s, l) => s + lineTax(l), 0), [lines]);
  const grandTotal = subtotal + taxAmount;
  // Same money either way -- only WHICH tax it is changes, and that decides the
  // return it is filed in. Inter-state = one IGST charge; within the state =
  // CGST + SGST, half each.
  const cgst = interstate ? 0 : taxAmount / 2;
  const sgst = interstate ? 0 : taxAmount - cgst;
  const igst = interstate ? taxAmount : 0;
  // Lines whose GST the product could not settle. Keyed on gstMissing -- the
  // SAME field the server warns on -- not on a separate derivation, so a line
  // that is taxed off a catalogue rate but carries NO HSN is still named here.
  const unresolvedGst = useMemo(
    () => lines.filter((l) => l.productId && (l.gstMissing || !l.gstResolved)),
    [lines],
  );

  const handleSubmit = async () => {
    if (!vendorId) {
      toast.error('Please select a vendor');
      return;
    }
    // A line is valid when it names a product -- picked from the catalogue OR
    // typed in for something we do not stock yet (ruling 13) -- with a positive
    // qty and cost.
    const named = (l: ComposerLine) => !!l.productId || !!l.newProduct;
    const valid = lines.filter((l) => named(l) && l.quantity >= 1 && l.unitCost > 0);
    const unpicked = lines.filter((l) => !named(l) && (l.quantity > 0 || l.unitCost > 0));
    if (unpicked.length > 0) {
      toast.error(
        'Every line needs a product — pick one, or use "Not in the catalogue?" to type it in',
      );
      return;
    }
    // Cost is required up front (it can't be backfilled at receiving) -- mirrors
    // both the manual form and the Buy Desk fix. Checked BEFORE the generic
    // "add a line" guard so a lone zero-cost line gets the precise, actionable
    // message rather than a vague one.
    const zeroCost = lines.filter((l) => named(l) && l.quantity >= 1 && !(l.unitCost > 0));
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
          ...(l.newProduct
            ? { new_product: l.newProduct }
            : { product_id: l.productId, product_name: l.productName, sku: l.sku || 'N/A' }),
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
            min={todayForDateInput()}
            onChange={(e) => setExpectedDate(e.target.value)}
            className="input-field"
            aria-label="Expected Delivery Date"
          />
          <p className="mt-1 text-xs text-gray-400">Today or later — a delivery cannot be promised in the past.</p>
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
              // UI/UX (owner item 3): the product gets a FULL-WIDTH row of its
              // own instead of being squeezed into 5 of 12 columns beside the
              // numbers. On a 40-line PO the buyer scans identities down one
              // column and numbers down another, and neither is truncated.
              <div key={index} className="p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
                <div>
                  <label className="block text-xs text-gray-600 mb-1">Product</label>
                  {renderProductCell({
                    line,
                    index,
                    pickProduct: (p) =>
                      setLines((prev) => prev.map((l, i) => (i === index ? applyPickedProduct(l, p) : l))),
                    clearProduct: () =>
                      setLines((prev) => prev.map((l, i) => (i === index ? releaseProduct(l) : l))),
                    setNewProduct: (np) =>
                      setLines((prev) =>
                        prev.map((l, i) =>
                          i === index
                            ? releaseProduct(l, {
                                newProduct: np,
                                productName: np ? `${np.brand} ${np.model}`.trim() : '',
                              })
                            : l,
                        ),
                      ),
                  })}
                </div>
                <div className="grid grid-cols-12 gap-2 items-start">
                  <div className="col-span-4 tablet:col-span-2">
                    <label className="block text-xs text-gray-600 mb-1">Qty</label>
                    <input
                      type="number"
                      min="1"
                      value={line.quantity}
                      // Audit F67: a tap selects the 1, so typing 4 gives 4, not 14.
                      onFocus={(e) => e.target.select()}
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
                      onFocus={(e) => {
                        e.target.select();
                        if (!line.costFocused) updateLine(index, { costFocused: true });
                      }}
                      onBlur={() =>
                        setLines((prev) =>
                          prev.map((l, i) => (i === index ? takeLastPaid({ ...l, costFocused: false }) : l)),
                        )
                      }
                      onChange={(e) =>
                        updateLine(index, {
                          unitCost: parseFloat(e.target.value) || 0,
                          costTouched: true,
                        })
                      }
                      className="input-field text-sm"
                      aria-label={`Unit cost for line ${index + 1}`}
                    />
                    {line.lastPaid ? (
                      <p className="mt-1 text-xs text-gray-400">
                        last paid {'₹'}
                        {line.lastPaid.unitPrice.toLocaleString('en-IN')}
                        {paidDate ? ` on ${paidDate}` : ''}
                      </p>
                    ) : null}
                  </div>
                  <div className="col-span-6 tablet:col-span-3">
                    <label className="block text-xs text-gray-600 mb-1">GST</label>
                    {line.productId && !line.gstResolved ? (
                      <p className="text-sm py-2 text-amber-700 flex items-center gap-1">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                        <span>Rate not set</span>
                      </p>
                    ) : (
                      <p className="text-sm text-gray-900 py-2">
                        {line.taxRate}%
                        {line.hsn ? (
                          <span className="text-xs text-gray-400"> · HSN {line.hsn}</span>
                        ) : null}
                        {line.productId && line.gstMissing ? (
                          <span className="block text-xs text-amber-700">No HSN</span>
                        ) : null}
                      </p>
                    )}
                  </div>
                  <div className="col-span-6 tablet:col-span-3 text-right">
                    <label className="block text-xs text-gray-600 mb-1">Line Total</label>
                    <p className="text-sm font-semibold text-gray-900 py-2">
                      {'₹'}
                      {lineTotal(line).toLocaleString('en-IN')}
                    </p>
                  </div>
                  <div className="col-span-12 tablet:col-span-1 flex justify-end">
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

      {/* Totals -- GST shown as it will actually be charged and filed */}
      <div className="flex justify-end">
        <div className="w-72 space-y-2 p-4 bg-gray-50 rounded-lg">
          <div className="flex justify-between text-sm">
            <span className="text-gray-600">Taxable value</span>
            <span className="font-medium text-gray-900">
              {'₹'}
              {subtotal.toLocaleString('en-IN')}
            </span>
          </div>
          {interstate ? (
            <div className="flex justify-between text-sm">
              <span className="text-gray-600">IGST</span>
              <span className="font-medium text-gray-900">
                {'₹'}
                {igst.toLocaleString('en-IN')}
              </span>
            </div>
          ) : (
            <>
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">CGST</span>
                <span className="font-medium text-gray-900">
                  {'₹'}
                  {cgst.toLocaleString('en-IN')}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">SGST</span>
                <span className="font-medium text-gray-900">
                  {'₹'}
                  {sgst.toLocaleString('en-IN')}
                </span>
              </div>
            </>
          )}
          <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
            <span className="text-gray-900">Grand Total</span>
            <span className="text-gray-900">
              {'₹'}
              {grandTotal.toLocaleString('en-IN')}
            </span>
          </div>
          <p className="text-xs text-gray-400 pt-1">
            {interstate === null
              ? 'Shown as a within-state purchase — add the GST number to this vendor and to this shop and the correct split will be used.'
              : interstate
                ? 'Different states — the vendor charges IGST.'
                : 'Same state — the vendor charges CGST + SGST.'}
          </p>
        </div>
      </div>

      {unresolvedGst.length > 0 ? (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-800">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <p className="font-medium">
              GST needs an HSN on {unresolvedGst.length}{' '}
              {unresolvedGst.length === 1 ? 'product' : 'products'}
            </p>
            <p className="text-xs mt-0.5">
              {unresolvedGst
                .map((l) => `${l.productName || l.sku} (${l.gstMissing || 'no GST rate'})`)
                .join(', ')}{' '}
              — add the HSN number on the product. An HSN is required on a purchase order, and
              until it is filled in the tax on any line with no rate is short.
            </p>
          </div>
        </div>
      ) : null}

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
