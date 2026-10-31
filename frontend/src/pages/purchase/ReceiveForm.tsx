// ============================================================================
// IMS 2.0 - Receive one delivery (the ONE receiving form)
// ============================================================================
// Every receipt is the same form: against an order, bought without a PO, or a
// delivery challan whose invoice comes later. The manager only COUNTS -- no
// price box (the accountant types the bill's prices on the Bills page; a
// "Bought without PO" line takes a cost only from the cost roles). No paper,
// no stock: the bill photo and number go in before the Receive button wakes.
// A clean box against an open order goes through the express door (which
// also drafts the bill and runs the 3-way match for accounts); anything else
// is created and accepted in two calls -- the rules live in receiveLines.ts.

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Loader2, Package, Printer, ScanLine, Trash2, X } from 'lucide-react';
import clsx from 'clsx';
import { vendorsApi } from '../../services/api/inventory';
import { grnCockpitApi, type UploadDocResult } from '../../services/api/grnCockpit';
import { productApi } from '../../services/api/products';
import { ApiError } from '../../services/api/client';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { COST_ROLES } from '../../components/common/CostCell';
import { UnitLabelsModal } from '../../components/labels/UnitLabelsModal';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { istDayString } from '../../utils/datetime';
import { getErrorDetail, getErrorMessage } from '../../utils/errorHandler';
import { AttachmentZone } from './AttachmentZone';
import { SupplierFormModal } from './SupplierFormModal';
import { heldLinesSummary, reportGrnAccept } from './grnAcceptToast';
import {
  expiryWarning,
  goesExpress,
  goodOf,
  grnItems,
  isExtraReceipt,
  isRejectOnlyOrder,
  lineNotes,
  linesFromPO,
  linesToSend,
  matchScan,
  newLine,
  productFacts,
  receiptSums,
  receiveProblem,
  rejectedOf,
  scanOne,
  tallyAll,
  type CandidatePO,
  type ReceiveKind,
  type ReceiveLine,
} from './receiveLines';

const DAMAGE_REASONS = ['Scratched lens', 'Broken hinge', 'Wrong colour', 'Wrong model', 'Wrong power'];

const istToday = () => istDayString(new Date()) ?? '';

/** The FastAPI detail off the error the api client delivers (an ApiError). */
function apiDetail(err: unknown): unknown {
  return getErrorDetail(err) ?? (getErrorMessage(err, '') || undefined);
}

interface Success {
  grnId: string;
  grnNumber: string;
  units: number;
  quarantined: number;
  held: string | null;
  poStatus: string | null;
  match: { match_status: string; exception_count: number } | null;
  accounts: string;
  creditLines: string[];
}

type Stage =
  | { kind: 'form' }
  | { kind: 'success'; s: Success }
  | { kind: 'blocked'; headline: string; message: string; grnNumber: string };

interface ReceiveFormProps {
  po: CandidatePO | null;
  initialKind: ReceiveKind;
  /** The Purchase shop in scope ('' = every shop, an admin). */
  storeId: string;
  ownStoreId: string;
  onClose: () => void;
  /** A receipt was saved: the page re-reads its lists. */
  onSaved: () => void;
  onOpenWaiting: (grnNumber: string) => void;
}

export function ReceiveForm({ po, initialKind, storeId, ownStoreId, onClose, onSaved, onOpenWaiting }: ReceiveFormProps) {
  const toast = useToast();
  const { user } = useAuth();
  const canEnterPrice = (user?.roles || []).some((r) => COST_ROLES.includes(r));

  const [kind, setKind] = useState<ReceiveKind>(initialKind);
  const [lines, setLines] = useState<ReceiveLine[]>(() => (po ? linesFromPO(po) : []));
  const [stage, setStage] = useState<Stage>({ kind: 'form' });
  const [labelsFor, setLabelsFor] = useState<Success | null>(null);

  // Header: who, which paper.
  const [vendorId, setVendorId] = useState('');
  const [vendors, setVendors] = useState<Array<{ vendor_id: string; name: string }>>([]);
  const [vendorsFailed, setVendorsFailed] = useState(false);
  const [addingDealer, setAddingDealer] = useState(false);
  const [billNo, setBillNo] = useState('');
  const [billDate, setBillDate] = useState(istToday);
  const [dcNumber, setDcNumber] = useState('');
  const [dcDate, setDcDate] = useState(istToday);
  const [upload, setUpload] = useState<UploadDocResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Lines: product search (no order), scan box, batch rows opened by hand.
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Array<Record<string, unknown>>>([]);
  const [searching, setSearching] = useState(false);
  const [scan, setScan] = useState('');
  const [batchOpen, setBatchOpen] = useState<Set<string>>(new Set());
  const [showCompleted, setShowCompleted] = useState(false);

  const rejectOnlyOrder = !!po && isRejectOnlyOrder(po);
  const extra = !!po && isExtraReceipt(po);
  const today = istToday();

  // Every supplier on file (switched off or not, up to the server's 1000):
  // the dealer quick-add refuses a name already on file, so each must be
  // pickable here.
  useEffect(() => {
    if (po) return;
    let cancelled = false;
    vendorsApi
      .getVendors({ limit: 1000 })
      .then((resp: unknown) => {
        if (cancelled) return;
        const r = resp as { vendors?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>;
        const rows = Array.isArray(r) ? r : r?.vendors || [];
        setVendors(
          rows
            .map((v) => ({
              vendor_id: String(v.vendor_id || v.id || ''),
              name: String(v.trade_name || v.legal_name || v.vendor_id || 'Vendor'),
            }))
            .filter((v) => v.vendor_id),
        );
      })
      .catch(() => !cancelled && setVendorsFailed(true));
    return () => {
      cancelled = true;
    };
  }, [po]);

  // What the catalogue knows about an order's lines: contact lens (asks for
  // batch + expiry), still a draft (held until catalogued), its barcodes.
  // ponytail: one GET per product; a bulk read when orders run to 50+ lines.
  useEffect(() => {
    if (!po) return;
    let cancelled = false;
    const ids = [...new Set(linesFromPO(po).map((l) => l.product_id))];
    void Promise.allSettled(ids.map((id) => productApi.getProduct(id))).then((res) => {
      if (cancelled) return;
      const facts = new Map(
        res.map((r, i) => [
          ids[i],
          r.status === 'fulfilled'
            ? productFacts(r.value)
            : r.reason instanceof ApiError && r.reason.status === 404
              ? { heldForCatalogue: true } // not in the catalogue: the server holds it
              : {},
        ] as const),
      );
      setLines((ls) => ls.map((l) => ({ ...l, ...facts.get(l.product_id) })));
    });
    return () => {
      cancelled = true;
    };
  }, [po]);

  // Product search for a receipt with no order (debounced).
  useEffect(() => {
    const q = query.trim();
    if (po || q.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const data = await productApi.getProducts({ search: q, limit: 10 });
        if (!cancelled) setResults((data?.products || []).slice(0, 10));
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, po]);

  const setLine = (key: string, patch: Partial<ReceiveLine>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  /** Adds a catalogue product as a typed line; returns its row key. */
  const addProduct = (p: Record<string, unknown>): string | null => {
    setQuery('');
    setResults([]);
    const pid = String(p.product_id ?? '');
    if (!pid) return null;
    const line: ReceiveLine = {
      ...newLine({ product_id: pid, product_name: String(p.name || p.product_name || pid), sku: p.sku ? String(p.sku) : undefined }, null),
      ...productFacts(p),
    };
    setLines((ls) => (ls.some((l) => l.product_id === pid) ? ls : [...ls, line]));
    return line.key;
  };

  // Each scan of a barcode / GTIN adds one to its line; the first scan on a
  // line starts its count from 1 (the prefilled "due" is not a count).
  const onScan = async () => {
    const code = scan.trim();
    setScan('');
    if (!code) return;
    const known = matchScan(lines, code);
    if (known >= 0) {
      const key = lines[known].key;
      setLines((ls) => ls.map((l) => (l.key === key ? scanOne(l) : l)));
      return;
    }
    const found = ((await productApi.getProducts({ search: code, limit: 5 }).catch(() => null))?.products ?? []) as Array<Record<string, unknown>>;
    const hit = !po && found.length === 1 ? found[0] : found.find((p) => lines.some((l) => l.product_id === p.product_id));
    const pid = hit ? String(hit.product_id ?? '') : '';
    if (!pid) {
      toast.warning(`${code} is not on this delivery`);
      return;
    }
    // Read the list as it is NOW: a scan fired before this lookup came back
    // may have added the product already -- count onto it, never reset it.
    setLines((ls) => {
      const i = ls.findIndex((l) => l.product_id === pid);
      if (i >= 0) return ls.map((l, j) => (j === i ? scanOne(l) : l));
      if (po) return ls;
      const line = newLine({ product_id: pid, product_name: String(hit?.name || hit?.product_name || pid), sku: hit?.sku ? String(hit.sku) : undefined }, null);
      return [...ls, scanOne({ ...line, ...productFacts(hit) })];
    });
  };

  const handleFile = async (file: File) => {
    setUploading(true);
    setUpload(null);
    try {
      const res = await grnCockpitApi.uploadDoc(file);
      if (!res.file_id) throw new Error('not stored');
      setUpload(res);
    } catch (err) {
      const d = apiDetail(err);
      toast.error(typeof d === 'string' && d !== 'not stored' ? d : 'Could not upload the bill - try again');
    } finally {
      setUploading(false);
    }
  };

  const problem = receiveProblem(
    { kind, hasOrder: !!po, vendorId, billNo, dcNumber, dcDate, billFileId: upload?.file_id ?? null },
    lines,
    rejectOnlyOrder,
  );

  const sums = useMemo(() => receiptSums(lines), [lines]);

  // ---- Submit ---------------------------------------------------------------

  const failed = (err: unknown) => {
    const detail = apiDetail(err);
    const d = (detail && typeof detail === 'object' ? detail : {}) as { code?: string; message?: string; grn_number?: string };
    if (d.code === 'GRN_DUPLICATE') {
      // This bill already has a receipt. The one thing not to do is invite
      // another attempt: name it and point at where to finish it.
      setStage({
        kind: 'blocked',
        headline: `This bill was already received${d.grn_number ? ` (${d.grn_number})` : ''}`,
        message: d.message ?? 'The same bill cannot be received twice.',
        grnNumber: d.grn_number ?? '',
      });
    } else if (d.code === 'EXPRESS_PARTIAL') {
      setStage({
        kind: 'blocked',
        headline: `Receipt ${d.grn_number ?? ''} was saved but is NOT all on the shelf yet`,
        message: d.message ?? 'Finish it from "Receipts still waiting".',
        grnNumber: d.grn_number ?? '',
      });
    } else if (d.code === 'ATTACHMENT_REQUIRED' || d.code === 'ATTACHMENT_INVALID') {
      setUpload(null);
      toast.error(d.message ?? 'The bill did not go through - attach it again.');
    } else {
      // "Try again" is only safe when nothing was created: a 409 means a
      // conflicting receipt exists, and repeating is the double-mint.
      const is409 = err instanceof ApiError && err.status === 409;
      toast.error(
        d.message ??
          (typeof detail === 'string'
            ? detail
            : is409
              ? 'Refused - this receipt may already exist. Check "Receipts still waiting"; do not submit it again.'
              : getErrorMessage(err, 'Could not save the receipt - try again.')),
        12000,
      );
    }
  };

  const creditLines = () => linesToSend(lines).filter((l) => rejectedOf(l) > 0 && l.damageChoice === 'credit_note' && !l.rejectOnly).map((l) => l.product_name);

  const submit = async () => {
    if (problem) {
      toast.error(problem);
      return;
    }
    // A receipt with no order is booked at the receiver's own shop (the
    // server's rule) -- never let it vanish from a list showing another shop.
    if (!po && (!ownStoreId || (!!storeId && storeId !== ownStoreId))) {
      toast.error('A receipt without a PO is recorded at your own shop: pick it in Shop to receive it');
      return;
    }
    // A challan stays at the receiver's own shop even when it names an order
    // (only a standard receipt is re-pointed to the order's shop).
    if (kind === 'DELIVERY_CHALLAN' && po?.delivery_store_id && po.delivery_store_id !== ownStoreId) {
      toast.error(
        "This order delivers to another shop, but a Delivery Challan is booked at the shop you are signed in to: switch to the order's shop in the top bar to receive it",
      );
      return;
    }
    const items = grnItems(kind, lines, canEnterPrice);
    setSubmitting(true);
    try {
      if (goesExpress(kind, po, lines) && po) {
        try {
          const r = await grnCockpitApi.expressReceive({
            po_id: po.po_id,
            vendor_invoice_no: billNo.trim(),
            vendor_invoice_date: billDate || undefined,
            items: items.map(({ po_item_id, product_id, received_qty, accepted_qty, location_code, batch_code, expiry_date }) => ({
              po_item_id, product_id, received_qty, accepted_qty, rejected_qty: 0, location_code, batch_code, expiry_date,
            })),
            attachment_file_id: upload?.file_id ?? '',
            attachment_filename: upload?.filename,
            attachment_mime: upload?.mime,
            notes: notes.trim() || undefined,
          });
          setStage({
            kind: 'success',
            s: {
              grnId: r.grn_id,
              grnNumber: r.grn_number,
              units: r.accepted_units ?? sums.good,
              quarantined: 0,
              held: null,
              poStatus: r.po_status,
              match: r.match_preview,
              accounts: r.match_preview ? '' : 'The bill is waiting for accounts to book it.',
              creditLines: [],
            },
          });
          onSaved();
          return;
        } catch (err) {
          const code = (apiDetail(err) as { code?: string } | undefined)?.code;
          // The server judged it not clean after all: the same lines take
          // the two-call door, which carries every discrepancy control.
          if (code !== 'EXPRESS_NOT_CLEAN' && code !== 'EXPRESS_STANDARD_ONLY') throw err;
        }
      }
      const created = await vendorsApi.createGRN({
        po_id: po?.po_id,
        grn_subtype: kind,
        dc_number: kind === 'DELIVERY_CHALLAN' ? dcNumber.trim() : undefined,
        dc_date: kind === 'DELIVERY_CHALLAN' ? dcDate : undefined,
        vendor_id: po ? undefined : vendorId,
        vendor_invoice_no: billNo.trim() || undefined,
        vendor_invoice_date: kind === 'DELIVERY_CHALLAN' ? istToday() : billDate || undefined,
        attachment_file_id: kind !== 'DELIVERY_CHALLAN' ? upload?.file_id || undefined : undefined,
        attachment_filename: kind !== 'DELIVERY_CHALLAN' ? upload?.filename : undefined,
        attachment_mime: kind !== 'DELIVERY_CHALLAN' ? upload?.mime : undefined,
        items,
        notes: notes.trim() || undefined,
      });
      const grnId = String(created?.grn_id || created?.id || '');
      const grnNumber = String(created?.grn_number || '');
      let acc: Record<string, unknown> | null = null;
      try {
        acc = await vendorsApi.acceptGRN(grnId);
      } catch (err) {
        onSaved();
        setStage({
          kind: 'blocked',
          headline: `Receipt ${grnNumber} was saved but is NOT on the shelf yet`,
          message: `${err instanceof Error ? err.message : 'Adding it to stock did not finish.'} Finish it from "Receipts still waiting" - do not receive the box again.`,
          grnNumber,
        });
        return;
      }
      if (acc?.status_flip_failed) {
        // Its pieces are on the shelf but the receipt did not finish: the
        // way out is accepting it again (counted once), never a new receipt.
        setStage({
          kind: 'blocked',
          headline: `Receipt ${grnNumber}: ${Number(acc.units_added ?? 0)} piece(s) are on the shelf, but the receipt did not finish`,
          message: `${String(acc.message || '')} Press "Add to stock" on it in "Receipts still waiting" - do not receive the box again.`.trim(),
          grnNumber,
        });
        return;
      }
      reportGrnAccept(toast, grnNumber, acc);
      onSaved();
      setStage({
        kind: 'success',
        s: {
          grnId,
          grnNumber,
          units: Number(acc?.units_added ?? 0),
          quarantined: Number(acc?.quarantined_units ?? 0),
          held: acc?.grn_status === 'PARTIALLY_ACCEPTED' ? heldLinesSummary(acc.unresolved_lines).text : null,
          poStatus: (acc?.po_status as string | null) ?? null,
          match: null,
          accounts:
            kind === 'NO_PO'
              ? 'Sent to accounts: a task to book this bill (GST credit only from a GST-registered dealer).'
              : kind === 'DELIVERY_CHALLAN'
                ? "Accounts match the vendor's invoice to this challan when it arrives."
                : 'Accounts book the bill against this receipt on the Bills page.',
          creditLines: creditLines(),
        },
      });
    } catch (err) {
      failed(err);
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Render: after the receipt ------------------------------------------

  if (stage.kind === 'blocked') {
    return (
      <div className="card" style={{ padding: 20 }}>
        <div className="rounded-lg px-4 py-4" style={{ border: '2px solid var(--warn)', background: 'rgba(245,158,11,0.08)' }} role="alert">
          <p className="font-bold text-sm mb-1" style={{ color: 'var(--ink)' }}>{stage.headline}</p>
          <p className="text-sm mb-3" style={{ color: 'var(--ink-2)' }}>{stage.message}</p>
          <div className="flex gap-2 flex-wrap">
            <button type="button" className="btn accent" onClick={() => onOpenWaiting(stage.grnNumber)}>
              <Package className="w-4 h-4" /> Open receipts still waiting
            </button>
            <button type="button" className="btn" onClick={onClose}>Back to deliveries</button>
          </div>
        </div>
      </div>
    );
  }

  if (stage.kind === 'success') {
    const s = stage.s;
    return (
      <div className="card" style={{ padding: 24 }}>
        {labelsFor && (
          <UnitLabelsModal
            grnId={labelsFor.grnId}
            title="Print stock labels"
            subtitle={`Receipt ${labelsFor.grnNumber}: one label per piece put on the shelf.`}
            onClose={() => setLabelsFor(null)}
          />
        )}
        <div className="flex items-start gap-3">
          <CheckCircle2 className="w-8 h-8 flex-shrink-0" style={{ color: s.held ? 'var(--warn)' : 'var(--ok)' }} />
          <div className="flex-1 min-w-0 space-y-1">
            <h3 className="font-semibold" style={{ color: 'var(--ink)' }}>
              {s.held ? 'Received - some lines are held.' : 'Received and put on the shelf.'}
            </h3>
            <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
              Goods receipt <span className="mono">{s.grnNumber}</span> · {s.units} piece{s.units === 1 ? '' : 's'} added to stock
              {s.quarantined > 0 && ` · ${s.quarantined} to quarantine, with a draft debit note for accounts`}
            </p>
            {s.held && <p className="text-sm" style={{ color: 'var(--warn)' }}>Held: {s.held}. They wait in "Receipts still waiting".</p>}
            {po && (
              <p className="text-xs flex items-center gap-2" style={{ color: 'var(--ink-4)' }}>
                Order {po.po_number} {s.poStatus && <PurchaseStatusChip status={s.poStatus} kind="po" />}
              </p>
            )}
            <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
              {s.match ? (
                <>
                  Sent to accounts: <span className="font-medium">{s.match.match_status}</span>
                  {s.match.exception_count > 0 && ` (${s.match.exception_count} exception${s.match.exception_count === 1 ? '' : 's'})`}
                </>
              ) : (
                s.accounts
              )}
            </p>
            {s.creditLines.length > 0 && (
              <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
                Credit asked for {s.creditLines.join(', ')}: the order still shows those pieces as due. If no more are
                coming, cancel what is still due on the <Link className="underline" to="/purchase/orders">order</Link>.
              </p>
            )}
            <div className="flex gap-3 pt-3 flex-wrap">
              <button type="button" className="btn" disabled={s.units === 0} onClick={() => setLabelsFor(s)}>
                <Printer className="w-4 h-4" /> Print {s.units} label{s.units === 1 ? '' : 's'}
              </button>
              <button type="button" className="btn accent" onClick={onClose}>Done</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---- Render: the form ------------------------------------------------------

  const kinds: Array<[ReceiveKind, string]> = po
    ? [
        ['STANDARD', `Against the order (${po.po_number})`],
        ...(rejectOnlyOrder ? [] : ([['DELIVERY_CHALLAN', 'Delivery challan, invoice comes later']] as Array<[ReceiveKind, string]>)),
      ]
    : [
        ['NO_PO', 'Bought without PO'],
        ['DELIVERY_CHALLAN', 'Delivery challan, invoice comes later'],
      ];
  const showDue = !!po;
  const priceCol = kind === 'NO_PO' && canEnterPrice;
  const cols = 8 + (priceCol ? 1 : 0);
  const ticked = lines.filter((l) => l.counted).length;
  // On an open order, a line already received in full (nothing due, nothing
  // counted) folds away; a scan or a count on it brings it back.
  const folds = (l: ReceiveLine) => !!po && !extra && !rejectOnlyOrder && l.due === 0 && l.arrived === 0;
  const completed = lines.filter(folds).length;
  const label = (t: string) => (
    <span className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }}>{t}</span>
  );

  return (
    <div className="space-y-4">
      {/* Who sent it, and the paper */}
      <div className="card">
        <div className="card-head">
          <h3 className="flex items-center gap-2 flex-wrap">
            <Package className="w-4 h-4" />
            {po ? (
              <>Receive <span className="mono">{po.po_number}</span> · {po.vendor_name}</>
            ) : (
              'Receive goods with no purchase order'
            )}
          </h3>
          <button type="button" className="btn sm" onClick={onClose}>
            <X className="w-3.5 h-3.5" /> Cancel
          </button>
        </div>
        <div className="card-body space-y-4">
          <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
            {kind === 'NO_PO' && canEnterPrice
              ? "Count what arrived, and type each item's cost per unit (before GST) from the dealer's bill. A manager's receipt has no cost: accounts type it on the Bills page."
              : 'You only count. Prices are never typed here: the accountant types them from the bill on the Bills page.'}
          </p>
          <div className="flex flex-wrap gap-x-5 gap-y-2" role="radiogroup" aria-label="What kind of receipt">
            {kinds.map(([k, text]) => (
              <label key={k} className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: 'var(--ink-2)' }}>
                <input type="radio" name="receive-kind" checked={kind === k} onChange={() => setKind(k)} />
                {text}
              </label>
            ))}
          </div>
          {kind === 'NO_PO' && (
            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
              Bought without PO: the dealer and the bill photo. GST credit only from a GST-registered dealer.
            </p>
          )}
          {rejectOnlyOrder && (
            <p className="text-xs" style={{ color: 'var(--ink-3)' }}>
              This order was closed by a cancel. Count what the vendor sent: every piece goes to quarantine with a draft
              return for accounts. Nothing becomes sellable and the order stays closed.
            </p>
          )}
          {extra && (
            <p className="text-xs" style={{ color: 'var(--ink-3)' }}>
              This order is fully received. Count only the extra goods in this delivery: they go into stock and the store manager is flagged.
              Cancelled items stay rejected.
            </p>
          )}

          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
            {!po && (
              <div className="tablet:col-span-3">
                <label htmlFor="receive-supplier">{label('Supplier (the dealer or vendor)')}</label>
                <div className="flex items-center gap-2 flex-wrap">
                  <select id="receive-supplier" value={vendorId} onChange={(e) => setVendorId(e.target.value)} className="input flex-1 min-w-0">
                    <option value="">Select the supplier…</option>
                    {vendors.map((v) => (
                      <option key={v.vendor_id} value={v.vendor_id}>{v.name}</option>
                    ))}
                  </select>
                  <button type="button" className="btn sm" onClick={() => setAddingDealer(true)}>
                    Not on the list? Add the dealer
                  </button>
                </div>
                {vendorsFailed && (
                  <p className="text-xs mt-1" style={{ color: 'var(--err)' }}>Could not load the supplier list — try again in a moment.</p>
                )}
              </div>
            )}
            {kind === 'DELIVERY_CHALLAN' && (
              <>
                <label>
                  {label('Challan no. *')}
                  <input className="input w-full" value={dcNumber} onChange={(e) => setDcNumber(e.target.value)} placeholder="e.g. DC/26/05/118" />
                </label>
                <label>
                  {label('Challan date')}
                  <input type="date" className="input w-full" value={dcDate} max={today} onChange={(e) => setDcDate(e.target.value)} />
                </label>
              </>
            )}
            <label>
              {label(kind === 'STANDARD' ? 'Vendor bill no. *' : kind === 'NO_PO' ? 'Bill no. (if any)' : 'Vendor bill no. (optional, arrives later)')}
              <input className="input w-full" value={billNo} onChange={(e) => setBillNo(e.target.value)} placeholder="e.g. JJ/26/05/2240" />
            </label>
            {kind !== 'DELIVERY_CHALLAN' && (
              <label>
                {label('Bill date')}
                <input type="date" className="input w-full" value={billDate} max={today} onChange={(e) => setBillDate(e.target.value)} />
              </label>
            )}
          </div>
          {kind !== 'DELIVERY_CHALLAN' ? (
            <div>
              {label('Bill photo or PDF (required) - no paper, no stock')}
              <AttachmentZone uploaded={upload} uploading={uploading} onSelect={handleFile} onRemove={() => setUpload(null)} />
            </div>
          ) : (
            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
              A challan needs its number; the vendor's bill is attached when it arrives, on the Bills page.
            </p>
          )}
        </div>
      </div>

      {/* What arrived */}
      <div className="card">
        <div className="card-head flex-wrap gap-2">
          <h3>What arrived · {ticked} of {lines.length} counted</h3>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="relative flex items-center">
              <ScanLine className="w-4 h-4 absolute left-2" style={{ color: 'var(--ink-4)' }} aria-hidden />
              <input
                className="input"
                style={{ paddingLeft: 28, width: 220 }}
                value={scan}
                onChange={(e) => setScan(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void onScan();
                  }
                }}
                placeholder="Scan a piece (optional)"
                aria-label="Scan a barcode"
              />
            </label>
            {lines.some((l) => (l.due ?? 0) > 0) && (
              <button type="button" className="btn accent" onClick={() => setLines(tallyAll)}>
                <CheckCircle2 className="w-4 h-4" />
                {lines.some((l) => l.touched) ? 'Everything else arrived as ordered' : 'Everything arrived as ordered'}
              </button>
            )}
          </div>
        </div>
        {!po && (
          <div className="card-body" style={{ paddingBottom: 0 }}>
            <div className="relative" style={{ maxWidth: 420 }}>
              <input
                className="input w-full"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search a product to add (name / SKU)…"
              />
              {searching && <Loader2 className="w-4 h-4 animate-spin absolute right-2 top-2.5" style={{ color: 'var(--ink-4)' }} />}
              {results.length > 0 && (
                <div className="absolute z-20 mt-1 w-full rounded-lg shadow-lg overflow-hidden" style={{ background: 'var(--panel, #fff)', border: '1px solid var(--line)' }}>
                  {results.map((p) => (
                    <button key={String(p.product_id)} type="button" className="block w-full text-left px-3 py-2 text-sm hover:bg-gray-50" onClick={() => addProduct(p)}>
                      <span style={{ color: 'var(--ink)' }}>{String(p.name || p.product_name || p.product_id)}</span>
                      {p.sku ? <span className="mono text-xs ml-2" style={{ color: 'var(--ink-4)' }}>{String(p.sku)}</span> : null}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {query.trim().length >= 2 && !searching && results.length === 0 && (
              <p className="text-xs mt-1" style={{ color: 'var(--ink-4)' }}>
                Nothing found — the item is not catalogued yet. Catalogue it first, then receive it.
              </p>
            )}
          </div>
        )}
        {lines.length === 0 ? (
          <p className="card-body text-sm" style={{ color: 'var(--ink-4)' }}>
            {po ? 'Nothing is left to receive on this order.' : 'Add each item that arrived, set how many, and tick it once counted.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="tbl min-w-[860px]">
              <thead>
                <tr>
                  <th>Product</th>
                  {showDue && <th className="right">Due</th>}
                  <th className="right">Arrived</th>
                  <th className="right">Damaged</th>
                  <th className="right">Into stock</th>
                  <th>Shelf</th>
                  {priceCol && <th className="right">Cost / unit before GST (₹)</th>}
                  <th>I counted this</th>
                  <th>Status</th>
                  {!showDue && <th />}
                </tr>
              </thead>
              <tbody>
                {lines.map((l, idx) => {
                  if (!showCompleted && folds(l)) return null;
                  const rej = rejectedOf(l);
                  const exp = expiryWarning(l.expiry, today);
                  const batchShown = l.contactLens || batchOpen.has(l.key);
                  return [
                    <tr key={l.key}>
                      <td>
                        <div className="font-medium" style={{ color: 'var(--ink)' }}>{l.product_name}</div>
                        <div className="mono text-xs" style={{ color: 'var(--ink-4)' }}>{l.sku || l.product_id}</div>
                        {batchShown ? (
                          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                            <input className="input" style={{ width: 110, height: 30 }} value={l.batch} placeholder="Batch / lot" aria-label={`Batch for ${l.product_name}`} onChange={(e) => setLine(l.key, { batch: e.target.value })} />
                            <input type="date" className="input" style={{ width: 140, height: 30 }} value={l.expiry} aria-label={`Expiry for ${l.product_name}`} onChange={(e) => setLine(l.key, { expiry: e.target.value })} />
                            {exp && (
                              <span className={clsx('chip', exp === 'expired' ? 'err' : 'warn')}>
                                {exp === 'expired' ? 'Expired: warning only' : 'Short-dated: warning only'}
                              </span>
                            )}
                          </div>
                        ) : (
                          <button type="button" className="text-xs underline mt-1" style={{ color: 'var(--ink-4)' }} onClick={() => setBatchOpen((s) => new Set(s).add(l.key))}>
                            + batch / expiry
                          </button>
                        )}
                      </td>
                      {showDue && <td className="right mono">{l.due}</td>}
                      <td className="right">
                        <input type="number" min={0} className="input" style={{ width: 64, textAlign: 'right' }} value={l.arrived} aria-label={`Arrived on line ${idx + 1}`} onChange={(e) => setLine(l.key, { arrived: Math.max(0, parseInt(e.target.value) || 0), touched: true })} />
                      </td>
                      <td className="right">
                        {l.rejectOnly ? (
                          <span className="mono" style={{ color: rej ? 'var(--err)' : 'var(--ink-4)' }}>{rej}</span>
                        ) : (
                          <input type="number" min={0} max={l.arrived} className="input" style={{ width: 64, textAlign: 'right' }} value={l.damaged} aria-label={`Damaged on line ${idx + 1}`} onChange={(e) => setLine(l.key, { damaged: Math.max(0, parseInt(e.target.value) || 0), touched: true })} />
                        )}
                      </td>
                      <td className="right mono">{goodOf(l)}</td>
                      <td>
                        <input className="input mono" style={{ width: 72 }} value={l.shelf} placeholder="C1" aria-label={`Shelf for ${l.product_name}`} onChange={(e) => setLine(l.key, { shelf: e.target.value.toUpperCase() })} />
                      </td>
                      {priceCol && (
                        <td className="right">
                          <input type="number" min={0} step="0.01" className="input" style={{ width: 96, textAlign: 'right' }} value={l.unitPrice ?? ''} aria-label={`Cost on line ${idx + 1}`} onChange={(e) => {
                            const v = parseFloat(e.target.value);
                            setLine(l.key, { unitPrice: v > 0 ? v : undefined });
                          }} />
                        </td>
                      )}
                      <td>
                        <input type="checkbox" className="w-5 h-5" checked={l.counted} aria-label={`I counted line ${idx + 1}: ${l.product_name}`} onChange={(e) => setLine(l.key, { counted: e.target.checked })} />
                      </td>
                      <td>
                        <div className="flex flex-col gap-1 items-start">
                          {lineNotes(l).map((n) => (
                            <span key={n.text} className={clsx('chip', n.tone)}>{n.text}</span>
                          ))}
                        </div>
                      </td>
                      {!showDue && (
                        <td className="right">
                          <button type="button" className="btn sm" title="Remove line" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}>
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      )}
                    </tr>,
                    rej > 0 && (
                      <tr key={`${l.key}-damage`}>
                        <td colSpan={cols} style={{ background: 'var(--err-50)' }}>
                          <div className="flex flex-wrap items-center gap-3">
                            <span className="text-sm font-medium" style={{ color: 'var(--err)' }}>
                              {l.rejectOnly ? 'Rejected pieces' : 'Damaged piece'}
                            </span>
                            <input
                              className="input"
                              style={{ minWidth: 180 }}
                              list="receive-damage-reasons"
                              value={l.damageReason}
                              placeholder="What's wrong? *"
                              aria-label={`What is wrong on line ${idx + 1}`}
                              onChange={(e) => setLine(l.key, { damageReason: e.target.value })}
                            />
                            {l.rejectOnly ? (
                              <span className="text-sm" style={{ color: 'var(--ink-3)' }}>
                                Credit from {po?.vendor_name || 'the supplier'}: the order line is cancelled, so no replacement is coming.
                              </span>
                            ) : (
                              <>
                              <span className="text-sm" style={{ color: 'var(--ink-3)' }}>From {po?.vendor_name || 'the supplier'} we want:</span>
                              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                                <input type="radio" name={`choice-${l.key}`} checked={l.damageChoice === 'replacement'} onChange={() => setLine(l.key, { damageChoice: 'replacement' })} />
                                Replacement coming
                              </label>
                              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                                <input type="radio" name={`choice-${l.key}`} checked={l.damageChoice === 'credit_note'} onChange={() => setLine(l.key, { damageChoice: 'credit_note' })} />
                                Credit
                              </label>
                              </>
                            )}
                          </div>
                          <p className="text-xs mt-1" style={{ color: 'var(--ink-3)' }}>
                            The piece goes to Quarantine (never on the shelf), and a draft debit note is made for accounts.
                          </p>
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
            {completed > 0 && (
              <button type="button" className="text-xs underline px-4 py-2" style={{ color: 'var(--ink-4)' }} onClick={() => setShowCompleted((v) => !v)}>
                {showCompleted ? 'Hide completed lines' : `Show ${completed} completed line${completed === 1 ? '' : 's'}`}
              </button>
            )}
            <datalist id="receive-damage-reasons">
              {DAMAGE_REASONS.map((r) => <option key={r} value={r} />)}
            </datalist>
          </div>
        )}
      </div>

      {/* What happens, then Receive */}
      <div className="card">
        <div className="card-body space-y-3">
          <div>
            <p className="text-sm font-medium mb-1" style={{ color: 'var(--ink)' }}>What happens when you press Receive</p>
            <ul className="text-sm space-y-0.5 list-disc pl-5" style={{ color: 'var(--ink-3)' }}>
              <li>{sums.onShelf} good piece{sums.onShelf === 1 ? '' : 's'} go into stock{lines.some((l) => l.shelf.trim()) ? ' at their shelf codes' : ''}.</li>
              {sums.rejected > 0 && <li>{sums.rejected} piece{sums.rejected === 1 ? '' : 's'} go to Quarantine; a draft debit note is made for accounts.</li>}
              {sums.short > 0 && <li>{sums.short} piece{sums.short === 1 ? '' : 's'} short stay open on the order.</li>}
              {sums.over > 0 && <li>{sums.over} of them {sums.over === 1 ? 'is' : 'are'} beyond the order: into stock, and the store manager is flagged.</li>}
              {sums.catalogue > 0 && (
                <li>
                  Another {sums.catalogue} piece{sums.catalogue === 1 ? '' : 's'} may wait until the catalogue team finishes
                  the product (an estimate: the server checks each product as it is received).
                </li>
              )}
              <li>
                {kind === 'DELIVERY_CHALLAN'
                  ? "The vendor's invoice is matched to this challan when it arrives."
                  : 'The bill goes to the accountant, who types the prices. Average cost changes only when the accountant accepts the bill.'}
              </li>
            </ul>
          </div>
          <label className="block">
            {label('Quality note (optional)')}
            <textarea className="input w-full resize-none" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anything worth noting about this delivery…" />
          </label>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm" style={{ color: 'var(--ink-3)' }}>
              Good pieces <b>{sums.good}</b> · Lines ticked "I counted this" <b>{ticked} of {lines.length}</b>
            </span>
            <span className="flex-1" />
            {problem && (
              <span className="text-xs flex items-center gap-1" style={{ color: 'var(--err)' }} data-testid="receive-problem">
                <AlertCircle className="w-3.5 h-3.5" /> {problem}
              </span>
            )}
            <button type="button" className="btn accent lg" disabled={!!problem || submitting || uploading} onClick={submit}>
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
              Receive {sums.arrived} piece{sums.arrived === 1 ? '' : 's'}
            </button>
          </div>
        </div>
      </div>

      {addingDealer && (
        <SupplierFormModal
          quick
          onClose={() => setAddingDealer(false)}
          onCreated={(dealer) => {
            setVendors((list) => [...list, { vendor_id: dealer.id, name: dealer.name }]);
            setVendorId(dealer.id);
            setAddingDealer(false);
          }}
        />
      )}
    </div>
  );
}
