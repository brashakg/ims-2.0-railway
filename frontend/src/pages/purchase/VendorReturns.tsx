// ============================================================================
// IMS 2.0 - Vendor Returns / Debit Notes
// ============================================================================
// Create and manage vendor returns for defective/damaged products

import { useState, useEffect, startTransition } from 'react';
import {
  Plus,
  X as XIcon,
  Package,
  IndianRupee,
  Calendar,
  ChevronDown,
  Clock,
} from 'lucide-react';
import clsx from 'clsx';
import { Link, useSearchParams } from 'react-router-dom';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { usePurchaseShop } from './purchaseShop';
import { RowShop } from './invoices/pickers';
import api from '../../services/api/client';
import { rtvDebitNotesApi, type DebitNote } from '../../services/api/rtvDebitNotes';
import { COST_ROLES } from '../../components/common/CostCell';
import { PURCHASE_ROLES } from './purchaseRoles';
import { errMsg } from './invoices/shared';
import { SupplierCreditConfirmation } from './SupplierCreditConfirmation';
import type { SupplierCreditResult } from '../../services/api/supplierCredits';

// A return line. Its price per piece is the accounts roles' to enter and to
// read (owner rulings 2026-10-12 R2 + R3): a manager raises the return with
// none, and anyone outside the accounts roles reads it without one -- the
// key is simply absent -> shown as "-". An accounts role prices an unpriced
// return with "Set prices" (PUT /vendor-returns/{id}/prices).
interface ReturnItem {
  product_id: string;
  product_name: string;
  quantity: number;
  reason: string;
  unit_price?: number | null;
}

type ReturnLine = ReturnItem;

/** One physical piece going back (a stock unit and its own barcode). */
interface Piece {
  stock_id: string;
  barcode: string;
  status?: string;
  expiry_date?: string | null;
  rejected?: boolean;
  no_receipt?: boolean;
}

/** A line on the create form: the pieces named, and the shop's pieces of the
 *  product found by SKU that can be picked. Owner decision 2026-10-18: a frame
 *  (every piece IMS holds has its own unit barcode) goes back piece by piece; the server
 *  (vendor_returns._pieces_for_lines) is the one rule, this only helps name
 *  them. */
interface FormLine extends ReturnItem {
  pieces: Piece[];
  options: Piece[];
}

const newLine = (): FormLine => ({
  product_id: '', product_name: '', quantity: 1, reason: 'defective', pieces: [], options: [],
});

/** GET /inventory/barcode/{code}: the scanned piece and its product. */
interface ScannedUnit {
  stock_id?: string;
  barcode?: string;
  status?: string;
  product_id: string;
  cross_store?: boolean;
  product?: { name?: string };
}

// A piece that can go back: on the shelf or already set aside. The status in
// the server's own spellings: none / IN_STOCK / any case = on the shelf.
const RETURNABLE = ['AVAILABLE', 'QUARANTINED'];
const pieceState = (raw?: string | null) => {
  const s = String(raw ?? '').trim().toUpperCase();
  return !s || s === 'IN_STOCK' ? 'AVAILABLE' : s;
};
// Dated stock (contact lenses, solutions): the pieces already set aside
// first, then the earliest expiry -- the order the form pre-ticks them in.
const pickOrder = (a: Piece, b: Piece) =>
  Number(a.status !== 'QUARANTINED') - Number(b.status !== 'QUARANTINED')
  || String(a.expiry_date || '9999').localeCompare(String(b.expiry_date || '9999'));

// Settled returns keep the prices they were settled at (the server refuses).
const SETTLED = ['credit_issued', 'replaced', 'cancelled'];
const isPriced = (ret: VendorReturn) =>
  ret.items.length > 0 && ret.items.every((it) => it.unit_price != null);

interface VendorReturn {
  return_id: string;
  vendor_id: string;
  vendor_name: string;
  store_id: string;
  items: ReturnLine[];
  return_type: 'credit_note' | 'replacement' | null;
  status: string;
  total_value?: number | null;
  receipt_rejection?: boolean;
  debit_note_draft_status?: string;
  issued_debit_note_id?: string;
  credit_note_number?: string | null;
  credit_note_amount?: number | null;
  created_at: string;
  created_by: string;
  notes: string;
  /** The pieces going back (barcodes; flagged when they have no receipt). */
  pieces?: Piece[];
}

interface Vendor {
  vendor_id: string;
  legal_name: string;
  trade_name: string;
  mobile: string;
}

const rupees = (v?: number | null) => (v == null ? '-' : `₹${v.toLocaleString('en-IN')}`);

const RETURN_REASONS = [
  { value: 'defective', label: 'Defective' },
  { value: 'wrong_item', label: 'Wrong Item' },
  { value: 'expired', label: 'Expired' },
  { value: 'damaged_in_transit', label: 'Damaged in Transit' },
  { value: 'quality_issue', label: 'Quality Issue' },
  { value: 'not_as_ordered', label: 'Not As Ordered' },
  { value: 'other', label: 'Other' },
];

// Muted semantic palette: in-progress states (created/approved/shipped) read as
// info blue, "received by vendor" as amber (awaiting credit), settled as green,
// cancelled as red. Decorative cyan/purple/orange are consolidated away.
const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-amber-50 text-amber-700',
  created: 'bg-blue-50 text-blue-700',
  approved: 'bg-blue-50 text-blue-700',
  shipped: 'bg-blue-50 text-blue-700',
  received_by_vendor: 'bg-amber-50 text-amber-700',
  credit_issued: 'bg-green-50 text-green-700',
  replaced: 'bg-green-50 text-green-700',
  cancelled: 'bg-red-50 text-red-700',
};

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  created: 'Created',
  approved: 'Approved',
  shipped: 'Shipped',
  received_by_vendor: 'Received by Vendor',
  credit_issued: 'Credit Issued',
  replaced: 'Replaced',
  cancelled: 'Cancelled',
};

// Supplier money (owner ruling 2026-10-01): the credit a supplier owes us on a
// return (its value, the credit-note amount and number) and the GST debit
// note's amounts are for COST_ROLES alone. The server already drops them
// for anyone else; these copies keep them off the screen whatever it sends.
// Owner ruling 2026-10-12 (R3): so are a return line's unit price, rate and
// value -- anyone else reads the items, quantities and reasons only. An
// allow-list, the server's own (cost_mask._RETURN_ITEM_KEYS, the one cut for
// every return line), so a price under any name stays off the screen.
const RETURN_LINE_KEYS = ['product_id', 'product_name', 'quantity', 'reason'] as const;

function withoutLinePrice(line: ReturnLine): ReturnLine {
  const out: Record<string, unknown> = {};
  for (const key of RETURN_LINE_KEYS) if (key in line) out[key] = line[key];
  return out as unknown as ReturnLine;
}

function withoutReturnCredit(ret: VendorReturn): VendorReturn {
  const out = { ...ret };
  delete out.total_value;
  delete out.credit_note_amount;
  delete out.credit_note_number;
  if (Array.isArray(ret.items)) out.items = ret.items.map(withoutLinePrice);
  return out;
}

function withoutDebitNoteMoney(dn: DebitNote): DebitNote {
  const out = { ...dn };
  delete out.totals_rupees;
  return out;
}

export function VendorReturns() {
  const toast = useToast();
  const { user } = useAuth();
  // audit F63: lists read the one Purchase scope; a new return is raised at
  // the caller's own shop.
  const { storeId: listScope, ownStoreId, showShopOf } = usePurchaseShop();
  // Supplier money is COST_ROLES' alone: everyone else reads the returns
  // (items, quantities, reasons, status) without what the supplier owes on them
  // or what a line is priced at, and RECORDING that money -- issuing the credit,
  // issuing the GST debit note -- is theirs too (owner ruling 2026-10-12). The
  // managers keep the goods side: raise, approve, ship, received by vendor, and
  // "replaced" on a replacement return.
  const canSeePayables = (user?.roles || []).some((r) => COST_ROLES.includes(r));
  const shownReturn = (r: VendorReturn) => (canSeePayables ? r : withoutReturnCredit(r));
  const shownNote = (dn: DebitNote) => (canSeePayables ? dn : withoutDebitNoteMoney(dn));
  // F21: the Quarantine Queue "Create RTV" CTA deep-links here with ?stock_id=...
  // so the new return physically links that quarantined unit (backend backfills
  // rtv_vendor_id). Without reading it, the advertised one-click linkage was dead.
  const [searchParams] = useSearchParams();
  const linkedVendorId = searchParams.get('vendor_id');
  const linkedStockId = searchParams.get('stock_id');
  const linkedBarcode = searchParams.get('barcode');
  const linkedReturnId = searchParams.get('return_id');
  // Workshop staff log a return (item, quantity, reason) and nothing more
  // (owner ruling 2026-10-12): moving it along -- approve, ship, received by
  // vendor -- is the purchase roles' (the server's _VENDOR_RETURN_ROLES), and
  // its price is the accounts roles' (canSeePayables, above).
  const canMoveGoods = (user?.roles || []).some((r) => PURCHASE_ROLES.includes(r));

  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active');
  const [isLoading, setIsLoading] = useState(true);
  const [returns, setReturns] = useState<VendorReturn[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [expandedReturn, setExpandedReturn] = useState<string | null>(linkedReturnId);
  const [draftChoices, setDraftChoices] = useState<Record<string, 'credit_note' | 'replacement' | ''>>({});
  const [confirmingDraft, setConfirmingDraft] = useState<string | null>(null);
  const [reopeningDraft, setReopeningDraft] = useState<string | null>(null);
  // F20: issued GST debit notes keyed by source return_id (one per return). The
  // physical return is unchanged; this is the formal debiting document layer.
  const [debitNotes, setDebitNotes] = useState<Record<string, DebitNote>>({});
  const [dnBusy, setDnBusy] = useState<string | null>(null);

  // Form state
  const [selectedVendor, setSelectedVendor] = useState('');
  const [returnType, setReturnType] = useState<'credit_note' | 'replacement'>('credit_note');
  const [items, setItems] = useState<FormLine[]>([newLine()]);
  const [scanText, setScanText] = useState<Record<number, string>>({});
  // One id per form: a double click names the same return (server dedupe).
  const [requestId, setRequestId] = useState('');
  const [notes, setNotes] = useState('');
  // Accounts pricing an unpriced return (one input per line, in line order).
  const [pricing, setPricing] = useState<VendorReturn | null>(null);
  const [priceInputs, setPriceInputs] = useState<string[]>([]);
  const [pricingBusy, setPricingBusy] = useState(false);
  const [confirmingCredit, setConfirmingCredit] = useState<string | null>(null);

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setIsLoading(true);

        // Fetch vendor returns and vendors in parallel
        const [returnsResp, vendorsResp] = await Promise.all([
          api.get('/vendor-returns/', {
            params: { store_id: listScope, limit: 100, ...(linkedVendorId ? { vendor_id: linkedVendorId } : {}) },
          }),
          api.get('/vendors/', { params: { limit: 100 } }),
        ]);

        const loaded: VendorReturn[] = (returnsResp.data.returns || [])
          .filter((ret: VendorReturn) => !linkedVendorId || ret.vendor_id === linkedVendorId).map(shownReturn);
        if (linkedReturnId && !loaded.some((r) => r.return_id === linkedReturnId)) {
          const linked = await api.get(`/vendor-returns/${encodeURIComponent(linkedReturnId)}`);
          const ret: VendorReturn = shownReturn(linked.data);
          if (linkedVendorId && ret.vendor_id !== linkedVendorId) return;
          if (showShopOf(ret.store_id)) return;
          loaded.unshift(ret);
        }
        setReturns(loaded);
        if (linkedReturnId) {
          setExpandedReturn(linkedReturnId);
          const linked = loaded.find((r) => r.return_id === linkedReturnId);
          if (linked) setActiveTab(SETTLED.includes(linked.status) ? 'history' : 'active');
        }
        setVendors(vendorsResp.data.vendors || vendorsResp.data.items || []);

        // F20: map any already-issued debit notes to their source return so the
        // row shows "Issued" + serial (fail-soft: a load error just hides them).
        try {
          const dnResp = await rtvDebitNotesApi.list({
            store_id: listScope,
            limit: 100,
          });
          const map: Record<string, DebitNote> = {};
          for (const dn of dnResp.debit_notes || []) {
            if (dn.rtv_ref_id) map[dn.rtv_ref_id] = shownNote(dn);
          }
          setDebitNotes(map);
        } catch {
          /* debit notes optional; ignore */
        }
      } catch {
        toast.error('Failed to load vendor returns');
      } finally {
        setIsLoading(false);
      }
    };

    loadData();
  }, [listScope, linkedReturnId, linkedVendorId]);

  const handleAddItem = () => {
    setItems([...items, newLine()]);
  };

  const handleRemoveItem = (idx: number) => {
    setItems(items.filter((_, i) => i !== idx));
    setScanText({});
  };

  const handleItemChange = (idx: number, field: keyof ReturnItem, value: any) => {
    const newItems = [...items];
    newItems[idx] = { ...newItems[idx], [field]: value };
    setItems(newItems);
  };

  // A line's pieces decide its product and its quantity.
  const setPieces = (idx: number, change: (line: FormLine) => Partial<FormLine>) =>
    setItems((cur) => cur.map((line, i) => {
      if (i !== idx) return line;
      const next = { ...line, ...change(line) };
      return next.pieces.length ? { ...next, quantity: next.pieces.length } : next;
    }));

  const togglePiece = (idx: number, piece: Piece) =>
    setPieces(idx, (line) => ({
      pieces: line.pieces.some((p) => p.stock_id === piece.stock_id)
        ? line.pieces.filter((p) => p.stock_id !== piece.stock_id)
        : [...line.pieces, piece],
    }));

  // Scan a piece's own barcode (or type a SKU / product barcode to list the
  // shop's pieces of that product to pick). The server checks every piece
  // again on create; this only names them.
  const scanLine = async (idx: number, code: string) => {
    const text = code.trim();
    if (!text) return;
    setScanText((s) => ({ ...s, [idx]: '' }));
    let unit: ScannedUnit | null = null;
    try {
      unit = (await api.get(`/inventory/barcode/${encodeURIComponent(text)}`, {
        params: { store_id: ownStoreId ?? undefined },
      })).data;
    } catch (e) {
      if ((e as { response?: { status?: number } })?.response?.status !== 404) {
        toast.error(errMsg(e, 'Could not look that barcode up'));
        return;
      }
    }
    if (unit?.stock_id) {
      const found = unit;
      const piece: Piece = {
        stock_id: unit.stock_id,
        barcode: unit.barcode || text,
        status: pieceState(unit.status),
      };
      const line = items[idx];
      if (found.cross_store) return toast.error(`Piece ${text} belongs to another shop.`);
      if (!RETURNABLE.includes(piece.status as string)) {
        return toast.error(`Piece ${text} is ${String(piece.status).toLowerCase()} - only a piece on the shelf or set aside can go back.`);
      }
      if (items.some((l) => l.pieces.some((p) => p.stock_id === piece.stock_id))) {
        return toast.error(`Piece ${text} is already on this return.`);
      }
      if (line.pieces.length && line.product_id !== found.product_id) {
        return toast.error('That piece is a different product - add it on a new line.');
      }
      setPieces(idx, (l) => ({
        product_id: found.product_id,
        product_name: found.product?.name || l.product_name || found.product_id,
        pieces: [...l.pieces, piece],
      }));
      return;
    }
    try {
      const found = (await api.get('/products', { params: { search: text, limit: 2 } })).data?.products || [];
      if (found.length !== 1) {
        return toast.error(found.length ? 'More than one product matches - scan the piece itself.' : `Nothing found for ${text}.`);
      }
      const product = found[0];
      const units = (await api.get('/inventory/units', {
        params: { product_id: product.product_id, store_id: ownStoreId ?? undefined },
      })).data?.units || [];
      const options: Piece[] = units
        .map((u: Piece) => ({ ...u, status: pieceState(u.status) }))
        .filter((u: Piece) => RETURNABLE.includes(u.status as string) && !u.rejected)
        .sort(pickOrder);
      const dated = options.some((u) => u.expiry_date);
      setPieces(idx, (l) => ({
        product_id: product.product_id,
        product_name: product.name || product.product_id,
        // Dated stock: pre-tick in pick order for the user to confirm/change.
        pieces: dated ? options.slice(0, Math.max(1, l.quantity)) : [],
        options,
      }));
    } catch (e) {
      toast.error(errMsg(e, 'Could not look that product up'));
    }
  };

  // The Quarantine Queue's "Create RTV" names its piece: start the form with it.
  const openCreate = () => {
    setShowModal(true);
    setRequestId(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`);
    if (linkedBarcode && !items[0].pieces.length && !items[0].product_id) {
      void scanLine(0, linkedBarcode);
    }
  };

  const handleCreateReturn = async () => {
    if (!selectedVendor || items.some(i => !i.product_name.trim() || i.quantity <= 0)) {
      toast.error('Please fill all required fields');
      return;
    }
    if (items.some((i) => !i.pieces.length) && !notes.trim()) {
      toast.error('An item with no piece scanned needs a note: say what it is and why it has no barcode.');
      return;
    }

    const vendor = vendors.find(v => v.vendor_id === selectedVendor);

    try {
      await api.post('/vendor-returns/', {
        vendor_id: selectedVendor,
        vendor_name: vendor?.legal_name || selectedVendor,
        store_id: ownStoreId ?? '',
        // A line's price is sent only by an accounts role (the server refuses
        // one from anyone else, R2 + R3); a blank price is left for later.
        items: items.map(({ unit_price, pieces, options: _options, ...i }) => ({
          ...i,
          product_id: i.product_id || i.product_name.toLowerCase().replace(/\s+/g, '-'),
          ...(pieces.length ? { stock_ids: pieces.map((p) => p.stock_id) } : {}),
          ...(canSeePayables && unit_price != null ? { unit_price } : {}),
        })),
        return_type: returnType,
        notes,
        client_request_id: requestId || undefined,
        // An older link without the barcode: the server joins the piece to
        // the line of its product.
        ...(linkedStockId && !items.some((l) => l.pieces.some((p) => p.stock_id === linkedStockId))
          ? { stock_ids: [linkedStockId] } : {}),
      });

      toast.success('Vendor return created successfully');
      setShowModal(false);
      setSelectedVendor('');
      setReturnType('credit_note');
      setItems([newLine()]);
      setScanText({});
      setNotes('');

      // Refresh the list -- an admin viewing another shop is shown the shop
      // the return was raised at (the list reloads with it), so it never vanishes.
      if (showShopOf(ownStoreId)) return;
      const refreshResp = await api.get('/vendor-returns/', {
        params: { store_id: listScope, limit: 100, ...(linkedVendorId ? { vendor_id: linkedVendorId } : {}) },
      });
      setReturns((refreshResp.data.returns || [])
        .filter((ret: VendorReturn) => !linkedVendorId || ret.vendor_id === linkedVendorId).map(shownReturn));
    } catch (e) {
      toast.error(errMsg(e, 'Failed to create vendor return'));
    }
  };

  // The form's total, for an accounts role, once every line is priced.
  const totalValue = items.every((item) => item.unit_price != null)
    ? items.reduce((sum, item) => sum + item.quantity * (item.unit_price || 0), 0)
    : null;
  const activeReturns = returns.filter(r => !['credit_issued', 'replaced', 'cancelled'].includes(r.status));
  const historyReturns = returns.filter(r => ['credit_issued', 'replaced', 'cancelled'].includes(r.status));

  const displayReturns = activeTab === 'active' ? activeReturns : historyReturns;

  const handleCreditSaved = (result: SupplierCreditResult) => {
    setReturns(current => current.map(ret => ret.return_id === result.return.return_id
      ? shownReturn({ ...ret, ...result.return } as VendorReturn) : ret));
    setConfirmingCredit(null);
    if (result.confirmation.posting_status === 'PENDING_EVIDENCE') {
      toast.success('Supplier note saved pending verification. Supplier dues and GST are unchanged.');
    } else if (result.summary.settlement_status === 'PARTIAL') {
      toast.success('Confirmed supplier credit posted. The remaining credit stays open.');
    } else {
      toast.success('Confirmed supplier credit posted.');
    }
  };

  const handleUpdateStatus = async (returnId: string, newStatus: string) => {
    try {
      const resp = await api.patch(`/vendor-returns/${returnId}/status`, { status: newStatus });
      const updated: VendorReturn = shownReturn(resp.data.return);
      setReturns(prev => prev.map(r => r.return_id === returnId ? updated : r));
      if (newStatus === 'draft') {
        setActiveTab('active');
        setExpandedReturn(returnId);
      }
      toast.success(`Status updated to ${STATUS_LABELS[newStatus] || newStatus}`);
    } catch (e) {
      toast.error(errMsg(e, 'Failed to update return status'));
    }
  };

  const handleReopenDraft = async (returnId: string) => {
    setReopeningDraft(returnId);
    try {
      await handleUpdateStatus(returnId, 'draft');
    } finally {
      setReopeningDraft(null);
    }
  };

  const handleConfirmDraft = async (ret: VendorReturn) => {
    const choice = draftChoices[ret.return_id] ?? ret.return_type;
    if (!choice) {
      toast.error('Choose credit note or replacement');
      return;
    }
    setConfirmingDraft(ret.return_id);
    try {
      const resp = await api.patch(`/vendor-returns/${ret.return_id}/status`, {
        status: 'created', return_type: choice,
      });
      const updated: VendorReturn = shownReturn(resp.data.return);
      setReturns((prev) => prev.map((r) => r.return_id === ret.return_id ? updated : r));
      toast.success('Return confirmed; the debit note is not yet issued');
    } catch (e) {
      toast.error(errMsg(e, 'Failed to confirm return'));
    } finally {
      setConfirmingDraft(null);
    }
  };

  const openPricing = (ret: VendorReturn) => {
    setPricing(ret);
    setPriceInputs(ret.items.map((it) => (it.unit_price == null ? '' : String(it.unit_price))));
  };

  const handleSavePrices = async () => {
    if (!pricing) return;
    const prices = priceInputs.map((v) => (v.trim() === '' ? NaN : Number(v)));
    if (prices.some((p) => !Number.isFinite(p) || p < 0)) {
      toast.error("Enter every line's price per piece");
      return;
    }
    setPricingBusy(true);
    try {
      const resp = await api.put(`/vendor-returns/${pricing.return_id}/prices`, {
        unit_prices: prices,
      });
      const updated: VendorReturn = shownReturn(resp.data.return);
      setReturns((prev) => prev.map((r) => (r.return_id === updated.return_id ? updated : r)));
      toast.success('Prices set');
      setPricing(null);
    } catch (e) {
      toast.error(errMsg(e, 'Failed to set the prices'));
    } finally {
      setPricingBusy(false);
    }
  };

  // F20: issue (or re-fetch the existing) GST debit note for a return. Idempotent
  // server-side -- a re-issue returns the first note with no new serial.
  const handleIssueDebitNote = async (returnId: string) => {
    setDnBusy(returnId);
    try {
      const res = await rtvDebitNotesApi.issue(returnId, 'vendor_return');
      setDebitNotes((prev) => ({ ...prev, [returnId]: shownNote(res.debit_note) }));
      toast.success(
        res.idempotent
          ? `Debit note already issued: ${res.debit_note.debit_note_number}`
          : `Debit note ${res.debit_note.debit_note_number} issued`,
      );
    } catch (e) {
      toast.error(errMsg(e, 'Failed to issue debit note (ADMIN / ACCOUNTANT only)'));
    } finally {
      setDnBusy(null);
    }
  };

  const handlePrintDebitNote = async (debitNoteId: string) => {
    try {
      const html = await rtvDebitNotesApi.fetchPrintHtml(debitNoteId);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(html);
        w.document.close();
      }
    } catch {
      toast.error('Failed to open debit note');
    }
  };

  const handleExportTally = async (debitNoteId: string, serial: string) => {
    try {
      const xml = await rtvDebitNotesApi.fetchTallyXml(debitNoteId);
      const blob = new Blob([xml], { type: 'application/xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${serial.replace(/[/\\]/g, '-')}-tally.xml`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error('Failed to export Tally voucher');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Vendor Returns</h1>
          <p className="text-gray-500">Manage defective products and debit notes</p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center gap-2 bg-bv-red-600 hover:bg-bv-red-700 text-white px-4 py-2 rounded-lg transition"
        >
          <Plus className="w-5 h-5" />
          Create Return
        </button>
      </div>

      {linkedVendorId && <p className="rounded border border-blue-100 bg-blue-50 p-3 text-sm text-blue-900">
        Showing this supplier’s returns. Open the relevant return and use Issue Credit to record the supplier’s confirmed note.{' '}
        <Link to="/purchase/vendor-returns" className="underline">Show all suppliers</Link>
      </p>}

      {/* Summary Stats */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Total Returns</p>
            <Package className="w-5 h-5 text-gray-500" />
          </div>
          <p className="text-2xl font-bold text-gray-900">{returns.length}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Pending Credits</p>
            <Clock className="w-5 h-5 text-amber-500" />
          </div>
          <p className="text-2xl font-bold text-amber-600">
            {returns.filter(r => r.status === 'received_by_vendor' || r.status === 'approved').length}
          </p>
        </div>
        {/* What suppliers owe us across these returns: COST_ROLES only. */}
        {canSeePayables && (
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Credit Value</p>
            <IndianRupee className="w-5 h-5 text-green-500" />
          </div>
          <p className="text-2xl font-bold text-green-600">
            {rupees(returns.reduce((sum, r) => sum + (r.credit_note_amount || 0), 0))}
          </p>
        </div>
        )}
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">This Month</p>
            <Calendar className="w-5 h-5 text-blue-500" />
          </div>
          <p className="text-2xl font-bold text-blue-600">
            {returns.filter(r => new Date(r.created_at).getMonth() === new Date().getMonth()).length}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-300">
        {(['active', 'history'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => startTransition(() => setActiveTab(tab))}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab === 'active' ? 'Active Returns' : 'History'}
          </button>
        ))}
      </div>

      {/* Returns List */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="text-center py-8 text-gray-500">Loading returns...</div>
        ) : displayReturns.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            {activeTab === 'active' ? 'No active returns' : 'No history'}
          </div>
        ) : (
          displayReturns.map((ret) => (
            <div
              key={ret.return_id}
              className="bg-white border border-gray-200 rounded-lg overflow-hidden hover:border-gray-300 transition"
            >
              {/* Header */}
              <button
                onClick={() => setExpandedReturn(expandedReturn === ret.return_id ? null : ret.return_id)}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-750 transition text-left"
              >
                <div className="flex items-center gap-4 flex-1">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="text-lg font-semibold text-gray-900">{ret.vendor_name}</h3>
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-semibold',
                        STATUS_COLORS[ret.status]
                      )}>
                        {STATUS_LABELS[ret.status]}
                      </span>
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-semibold',
                        ret.return_type !== 'replacement' ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-700'
                      )}>
                        {ret.return_type === 'replacement' ? 'Replacement' : ret.status === 'draft' && !ret.return_type ? 'Choose return type' : 'Credit Note'}
                      </span>
                    </div>
                    <p className="text-gray-500 text-sm">Return ID: {ret.return_id}</p>
                    {/* On All stores each return names the shop it was raised at (r3 #10). */}
                    {!listScope && <RowShop storeId={ret.store_id} />}
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-gray-900">
                      {canSeePayables && ret.total_value == null ? 'Not priced' : rupees(ret.total_value)}
                    </p>
                    <p className="text-gray-500 text-sm">{ret.items.length} item(s)</p>
                  </div>
                </div>
                <ChevronDown
                  className={clsx(
                    'w-5 h-5 text-gray-500 transition-transform',
                    expandedReturn === ret.return_id && 'rotate-180'
                  )}
                />
              </button>

              {/* Expanded Details */}
              {expandedReturn === ret.return_id && (
                <div className="border-t border-gray-200 px-6 py-4 bg-gray-50 space-y-4">
                  {ret.receipt_rejection && ret.status === 'cancelled' && (
                    <div className="rounded border border-amber-200 bg-amber-50 p-3 space-y-2">
                      <p className="text-sm text-amber-800">Rejected pieces remain in quarantine.</p>
                      {canMoveGoods && !debitNotes[ret.return_id] && !ret.issued_debit_note_id && ret.debit_note_draft_status !== 'ISSUED' && (
                        <button
                          onClick={() => handleReopenDraft(ret.return_id)}
                          disabled={reopeningDraft === ret.return_id}
                          className="bg-gray-900 text-white rounded px-3 py-2 text-sm disabled:opacity-50"
                        >
                          {reopeningDraft === ret.return_id ? 'Reopening...' : 'Reopen draft'}
                        </button>
                      )}
                    </div>
                  )}
                  {ret.status === 'draft' && (
                    <div className="rounded border border-amber-200 bg-amber-50 p-3 space-y-2">
                      <p className="text-sm text-amber-800">Draft — Accounts review. No debit note has been issued.</p>
                      {canMoveGoods && (
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="text-sm text-gray-700" htmlFor={`draft-type-${ret.return_id}`}>Return type</label>
                          <select
                            id={`draft-type-${ret.return_id}`}
                            value={draftChoices[ret.return_id] ?? ret.return_type ?? ''}
                            onChange={(e) => setDraftChoices((prev) => ({ ...prev, [ret.return_id]: e.target.value as 'credit_note' | 'replacement' | '' }))}
                            className="border border-gray-300 rounded px-3 py-2 text-sm bg-white"
                          >
                            <option value="">Choose credit or replacement</option>
                            <option value="credit_note">Credit note</option>
                            <option value="replacement">Replacement</option>
                          </select>
                          <button
                            onClick={() => handleConfirmDraft(ret)}
                            disabled={confirmingDraft === ret.return_id || !(draftChoices[ret.return_id] ?? ret.return_type)}
                            className="bg-gray-900 text-white rounded px-3 py-2 text-sm disabled:opacity-50"
                          >
                            {confirmingDraft === ret.return_id ? 'Confirming...' : 'Confirm return'}
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {/* Items */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-semibold text-gray-900">Items</h4>
                      {/* Pricing the lines records the supplier money the
                          credit and the debit note are built from: the
                          accounts roles only, until the return is settled or
                          its debit note issued (the server refuses after). */}
                      {canSeePayables && !SETTLED.includes(ret.status) && !debitNotes[ret.return_id] && (
                        <button
                          onClick={() => openPricing(ret)}
                          className="border border-gray-300 hover:bg-gray-100 text-gray-700 px-3 py-1.5 rounded text-sm font-medium transition"
                        >
                          {isPriced(ret) ? 'Edit prices' : 'Set prices'}
                        </button>
                      )}
                    </div>
                    <div className="space-y-2">
                      {ret.items.map((item, idx) => (
                        <div key={idx} className="flex justify-between items-center text-sm">
                          <div>
                            <p className="text-gray-600">{item.product_name}</p>
                            <p className="text-gray-500 text-xs">
                              Qty: {item.quantity}
                              {item.unit_price != null && ` @ ${rupees(item.unit_price)}`}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-gray-600 font-medium">
                              {rupees(item.unit_price == null ? null : item.quantity * item.unit_price)}
                            </p>
                            <p className="text-gray-500 text-xs">{RETURN_REASONS.find(r => r.value === item.reason)?.label || item.reason}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                    {!!ret.pieces?.length && (
                      <p className="mt-2 text-xs text-gray-600">
                        Pieces: {ret.pieces.map((p) => `${p.barcode || p.stock_id}${p.no_receipt ? ' (no receipt - supplier not checked)' : ''}`).join(', ')}
                      </p>
                    )}
                  </div>

                  {/* Notes & Credit Info */}
                  <div className="grid grid-cols-2 gap-4">
                    {ret.notes && (
                      <div>
                        <p className="text-gray-500 text-sm mb-1">Notes</p>
                        <p className="text-gray-600 text-sm">{ret.notes}</p>
                      </div>
                    )}
                    {ret.credit_note_number && (
                      <div>
                        <p className="text-gray-500 text-sm mb-1">Credit Note</p>
                        <p className="text-green-600 font-semibold">{ret.credit_note_number}</p>
                      </div>
                    )}
                  </div>

                  {/* F20: GST Debit Note (the formal document debiting the vendor) */}
                  <div className="pt-4 border-t border-gray-300">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-gray-500 text-sm mb-1">GST Debit Note</p>
                        {debitNotes[ret.return_id] ? (
                          <p className="text-gray-900 font-semibold">
                            {debitNotes[ret.return_id].debit_note_number}
                            {canSeePayables && debitNotes[ret.return_id].totals_rupees && (
                              <span className="ml-2 text-gray-500 font-normal text-sm">
                                {rupees(debitNotes[ret.return_id].totals_rupees?.grand_total)}
                                {debitNotes[ret.return_id].is_inter_state ? ' (IGST)' : ' (CGST+SGST)'}
                              </span>
                            )}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm">{ret.debit_note_draft_status === 'DRAFT' ? 'Draft — Accounts review' : 'Not yet issued'}</p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        {debitNotes[ret.return_id] ? (
                          // Print + Export Tally are the note's amounts end to
                          // end: COST_ROLES only (the server 403s the rest).
                          canSeePayables && (
                          <>
                            <button
                              onClick={() =>
                                handlePrintDebitNote(debitNotes[ret.return_id].debit_note_id)
                              }
                              className="border border-gray-300 hover:bg-gray-100 text-gray-700 px-3 py-1.5 rounded text-sm font-medium transition"
                            >
                              Print
                            </button>
                            {/* The Tally voucher is nothing but the note's
                                money: accounts only (owner ruling 2026-10-12). */}
                            {canSeePayables && (
                              <button
                                onClick={() =>
                                  handleExportTally(
                                    debitNotes[ret.return_id].debit_note_id,
                                    debitNotes[ret.return_id].debit_note_number,
                                  )
                                }
                                className="border border-gray-300 hover:bg-gray-100 text-gray-700 px-3 py-1.5 rounded text-sm font-medium transition"
                              >
                                Export Tally
                              </button>
                            )}
                          </>
                          )
                        ) : ret.receipt_rejection && ret.status === 'cancelled' ? (
                          <p className="text-gray-500 text-xs">Reopen and confirm the return before issuing a debit note</p>
                        ) : ret.status === 'draft' ? (
                          <p className="text-gray-500 text-xs">Confirm the return before issuing a debit note</p>
                        ) : canSeePayables ? (
                          // Issuing the note records what the supplier owes us:
                          // COST_ROLES only (the server 403s the rest).
                          <button
                            disabled={dnBusy === ret.return_id || !isPriced(ret)}
                            title={isPriced(ret) ? undefined : 'Set the prices first'}
                            onClick={() => handleIssueDebitNote(ret.return_id)}
                            className="bg-gray-900 hover:bg-gray-800 disabled:opacity-50 text-white px-3 py-1.5 rounded text-sm font-medium transition"
                          >
                            {dnBusy === ret.return_id ? 'Issuing...' : 'Issue Debit Note'}
                          </button>
                        ) : (
                          <p className="text-gray-500 text-xs">Accounts issue the debit note</p>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Action Buttons: approve / ship / received are the purchase
                      roles' (the server refuses anyone else); the credit is the
                      accounts roles' (below). */}
                  {activeTab === 'active' && canMoveGoods && (
                    <div className="flex gap-2 pt-4 border-t border-gray-300">
                      {ret.status === 'created' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'approved')}
                          className="flex-1 bg-green-600 hover:bg-green-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Approve
                        </button>
                      )}
                      {ret.status === 'approved' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'shipped')}
                          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Mark as Shipped
                        </button>
                      )}
                      {ret.status === 'shipped' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'received_by_vendor')}
                          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Received by Vendor
                        </button>
                      )}
                      {ret.status === 'received_by_vendor' && (
                        <>
                          {/* Issuing the credit records the supplier's credit
                              note and its amount: COST_ROLES only (owner
                              ruling 2026-10-12; the server 403s the rest). */}
                          {canSeePayables ? (
                            // Actual supplier notes can remain pending while
                            // Accounts verifies missing original-bill evidence.
                            <button
                              onClick={() => setConfirmingCredit(ret.return_id)}
                              className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-2 rounded text-sm font-medium transition"
                            >
                              Issue Credit
                            </button>
                          ) : (
                            <p className="flex-1 self-center text-gray-500 text-xs">
                              Accounts record the supplier's credit
                            </p>
                          )}
                          {/* 'replaced' is the goods coming back only on a
                              replacement return. On a credit-note return it
                              closes the return for good WITHOUT the credit the
                              supplier owes -- forgoing supplier money, so the
                              accounts roles' call (the server refuses anyone
                              else: vendor_returns.update_return_status). */}
                          {(ret.return_type === 'replacement' || canSeePayables) && (
                            <button
                              onClick={() => handleUpdateStatus(ret.return_id, 'replaced')}
                              className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                            >
                              Mark as Replaced
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Create Return Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-2xl max-h-[90dvh] overflow-y-auto border border-gray-200 shadow-2xl">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Create Vendor Return</h2>
              <button
                onClick={() => setShowModal(false)}
                className="text-gray-500 hover:text-gray-700 transition"
              >
                <XIcon className="w-6 h-6" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-6 space-y-4">
              {/* Vendor Selection */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Vendor *</label>
                <select
                  value={selectedVendor}
                  onChange={(e) => setSelectedVendor(e.target.value)}
                  className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 hover:bg-gray-100 focus:border-blue-500 outline-none transition"
                >
                  <option value="">Select Vendor...</option>
                  {vendors.map((v) => (
                    <option key={v.vendor_id} value={v.vendor_id}>
                      {v.legal_name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Return Type */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Return Type *</label>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="credit_note"
                      checked={returnType === 'credit_note'}
                      onChange={(e) => setReturnType(e.target.value as 'credit_note')}
                      className="w-4 h-4"
                    />
                    <span className="text-gray-900">Credit Note</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="replacement"
                      checked={returnType === 'replacement'}
                      onChange={(e) => setReturnType(e.target.value as 'replacement')}
                      className="w-4 h-4"
                    />
                    <span className="text-gray-900">Replacement</span>
                  </label>
                </div>
              </div>

              {/* Items */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-gray-900">Items *</label>
                  <button
                    onClick={handleAddItem}
                    className="text-blue-600 hover:text-blue-700 text-sm font-medium"
                  >
                    + Add Item
                  </button>
                </div>
                <div className="space-y-3">
                  {items.map((item, idx) => (
                    <div key={idx} className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
                      {/* Owner decision 2026-10-18: a frame goes back piece by
                          piece -- scan each one's own barcode, or find the
                          product by SKU and tick its pieces. */}
                      <div>
                        <label className="block text-xs text-gray-500 mb-1">Scan each piece (or type a SKU to pick)</label>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={scanText[idx] ?? ''}
                            onChange={(e) => setScanText((s) => ({ ...s, [idx]: e.target.value }))}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                void scanLine(idx, scanText[idx] ?? '');
                              }
                            }}
                            placeholder="Piece barcode or SKU"
                            className="flex-1 min-w-0 px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm placeholder-gray-500 outline-none focus:border-blue-500"
                          />
                          <button
                            type="button"
                            onClick={() => void scanLine(idx, scanText[idx] ?? '')}
                            className="px-3 py-2 rounded border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-100"
                          >
                            Add
                          </button>
                        </div>
                        {item.pieces.length > 0 && (
                          <div className="flex flex-wrap gap-2 mt-2">
                            {item.pieces.map((p) => (
                              <span key={p.stock_id} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-white border border-gray-300 text-xs text-gray-800">
                                {p.barcode}
                                <button type="button" aria-label={`Remove ${p.barcode}`} onClick={() => togglePiece(idx, p)} className="text-gray-500 hover:text-red-600">
                                  <XIcon className="w-3 h-3" />
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                        {item.options.length > 0 && (
                          <div className="mt-2 max-h-32 overflow-y-auto border border-gray-200 rounded bg-white p-2 space-y-1">
                            {item.options.map((p) => (
                              <label key={p.stock_id} className="flex items-center gap-2 text-xs text-gray-800 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={item.pieces.some((x) => x.stock_id === p.stock_id)}
                                  onChange={() => togglePiece(idx, p)}
                                />
                                {p.barcode}
                                <span className="text-gray-500">{p.status === 'QUARANTINED' ? 'set aside' : 'on shelf'}</span>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Product Name</label>
                          <input
                            type="text"
                            value={item.product_name}
                            readOnly={!!item.product_id}
                            onChange={(e) => handleItemChange(idx, 'product_name', e.target.value)}
                            placeholder="Enter product name"
                            title={item.product_id ? undefined : 'Only for something not in stock, e.g. a made-to-order lens'}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm placeholder-gray-500 outline-none focus:border-blue-500 read-only:bg-gray-100"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Quantity</label>
                          <input
                            type="number"
                            min="1"
                            value={item.quantity}
                            readOnly={item.pieces.length > 0}
                            onChange={(e) => handleItemChange(idx, 'quantity', parseInt(e.target.value) || 0)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500 read-only:bg-gray-100"
                          />
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        {/* The price per piece: the accounts roles' to enter
                            (R2 + R3). A manager logs the goods only. */}
                        {canSeePayables && (
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Unit Price</label>
                          <input
                            type="number"
                            min="0"
                            value={item.unit_price ?? ''}
                            onChange={(e) =>
                              handleItemChange(
                                idx,
                                'unit_price',
                                e.target.value === '' ? undefined : parseFloat(e.target.value) || 0,
                              )
                            }
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                          />
                        </div>
                        )}
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Reason</label>
                          <select
                            value={item.reason}
                            onChange={(e) => handleItemChange(idx, 'reason', e.target.value)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                          >
                            {RETURN_REASONS.map((r) => (
                              <option key={r.value} value={r.value}>
                                {r.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {items.length > 1 && (
                        <button
                          onClick={() => handleRemoveItem(idx)}
                          className="text-red-600 hover:text-red-700 text-sm font-medium"
                        >
                          Remove Item
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Notes */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Notes</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Add any additional notes..."
                  rows={3}
                  className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 text-sm placeholder-gray-500 outline-none focus:border-blue-500 resize-none"
                />
              </div>

              {/* Total: the supplier money the return is worth -- the accounts
                  roles' alone (R3). A manager is told who prices it. */}
              {canSeePayables ? (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <div className="flex justify-between items-center">
                    <p className="text-gray-600 font-medium">Total Return Value</p>
                    <p className="text-2xl font-bold text-gray-900">
                      {totalValue == null ? 'Not priced' : rupees(totalValue)}
                    </p>
                  </div>
                </div>
              ) : (
                <p className="text-gray-500 text-xs">
                  Accounts set the price per piece and record the supplier's credit.
                </p>
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex gap-3 px-6 py-4 border-t border-gray-200 bg-gray-50">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-medium transition border border-gray-300"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateReturn}
                className="flex-1 px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white rounded-lg font-medium transition"
              >
                Create Return
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Set prices (accounts roles) */}
      {pricing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div
            role="dialog"
            aria-label="Set prices"
            className="bg-white rounded-lg w-full max-w-lg max-h-[90dvh] overflow-y-auto border border-gray-200 shadow-2xl"
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Set prices</h2>
              <button onClick={() => setPricing(null)} className="text-gray-500 hover:text-gray-700 transition">
                <XIcon className="w-6 h-6" />
              </button>
            </div>
            <div className="p-6 space-y-3">
              <p className="text-gray-500 text-sm">
                Price per piece for each line of {pricing.return_id}. The credit and the GST debit note are
                built from these.
              </p>
              {pricing.items.map((it, idx) => (
                <label key={idx} className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-gray-700">
                    {it.product_name} <span className="text-gray-500">x {it.quantity}</span>
                  </span>
                  <input
                    type="number"
                    min="0"
                    aria-label={`Price per piece for ${it.product_name}`}
                    value={priceInputs[idx] ?? ''}
                    onChange={(e) =>
                      setPriceInputs((prev) => prev.map((v, i) => (i === idx ? e.target.value : v)))
                    }
                    className="w-36 px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                  />
                </label>
              ))}
            </div>
            <div className="flex gap-3 px-6 py-4 border-t border-gray-200 bg-gray-50">
              <button
                onClick={() => setPricing(null)}
                className="flex-1 px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-medium transition border border-gray-300"
              >
                Cancel
              </button>
              <button
                disabled={pricingBusy}
                onClick={handleSavePrices}
                className="flex-1 px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 disabled:opacity-50 text-white rounded-lg font-medium transition"
              >
                {pricingBusy ? 'Saving...' : 'Save prices'}
              </button>
            </div>
          </div>
        </div>
      )}
      {confirmingCredit && <SupplierCreditConfirmation
        key={`${confirmingCredit}:${listScope ?? ''}`}
        returnId={confirmingCredit}
        canRecord={canSeePayables && returns.some(ret => ret.return_id === confirmingCredit && (!listScope || ret.store_id === listScope))}
        onClose={() => setConfirmingCredit(null)}
        onSaved={handleCreditSaved}
      />}
    </div>
  );
}
