// ============================================================================
// IMS 2.0 - Receipt history on the receiving screen (with the GRN print)
// ============================================================================
// The newest page of goods receipts in the Purchase shop scope, each with what
// it was against, who received it, accept / reject, and a Print GRN door.
// "Only with problems" is the old Discrepancies tab: rejected or short-accepted.

import { useEffect, useState } from 'react';
import { FileText, Printer } from 'lucide-react';
import clsx from 'clsx';
import { vendorsApi } from '../../services/api/inventory';
import { GRNPrint } from '../../components/print/GRNPrint';
import { resolveStoreIdentity, type StoreIdentity } from '../../components/print/storeIdentity';
import { matchingTotal } from './purchaseQueries';
import { RowShop } from './invoices/pickers';

interface HistoryItem {
  product_id: string;
  product_name?: string;
  /** What the order asked for, stamped on the line by the server. */
  ordered_qty?: number;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason?: string;
}

interface HistoryGRN {
  id: string;
  grn_number: string;
  po_number: string;
  /** "Against <PO>", "Bought without PO · dealer" or "Delivery challan <no>". */
  source: string;
  received_at: string;
  total_received: number;
  total_accepted: number;
  total_rejected: number;
  items: HistoryItem[];
  quality: 'passed' | 'failed' | 'conditional';
  received_by: string;
  store_id?: string;
  vendor_id?: string;
  vendor_name?: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toHistoryGRN(g: any): HistoryGRN {
  const items: HistoryItem[] = Array.isArray(g.items) ? g.items : [];
  const sum = (k: keyof HistoryItem) => items.reduce((s, i) => s + (Number(i[k]) || 0), 0);
  const rec = g.total_received ?? sum('received_qty');
  const acc = g.total_accepted ?? sum('accepted_qty');
  const rej = g.total_rejected ?? sum('rejected_qty');
  const noPo = g.grn_subtype === 'NO_PO';
  const dc = g.grn_subtype === 'DELIVERY_CHALLAN';
  return {
    id: g.grn_id || g.id || g._id,
    grn_number: g.grn_number,
    po_number: g.po_number || (noPo ? 'Bought without PO' : ''),
    source: g.po_number
      ? `Against ${g.po_number}`
      : noPo
        ? `Bought without PO${g.vendor_name ? ` · ${g.vendor_name}` : ''}`
        : dc
          ? `Delivery challan${g.dc_number ? ` ${g.dc_number}` : ''}`
          : 'Against Unknown PO',
    received_at: g.received_at || g.created_at,
    total_received: rec,
    total_accepted: acc,
    total_rejected: rej,
    items,
    quality: rej === 0 ? 'passed' : acc === 0 ? 'failed' : 'conditional',
    received_by: g.created_by_name || g.created_by || 'Unknown',
    store_id: g.store_id,
    vendor_id: g.vendor_id || undefined,
    vendor_name: g.vendor_name || undefined,
  };
}

const CHIP = { passed: 'ok', failed: 'err', conditional: 'warn' } as const;
const WORD = { passed: 'Passed', failed: 'Failed', conditional: 'Conditional' } as const;

export function ReceiptHistory({ storeId, refreshKey }: { storeId: string; refreshKey: number }) {
  const [grns, setGrns] = useState<HistoryGRN[]>([]);
  const [total, setTotal] = useState(0);
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [printGrn, setPrintGrn] = useState<HistoryGRN | null>(null);
  const [identity, setIdentity] = useState<StoreIdentity | null>(null);
  const [vendor, setVendor] = useState<{ address: string; gstin: string } | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    vendorsApi
      .getGRNs({ store_id: storeId || undefined })
      .then((resp: unknown) => {
        if (cancelled) return;
        const r = resp as { grns?: unknown[]; data?: unknown[] } | unknown[];
        const list = (Array.isArray(r) ? r : r?.grns || r?.data || []) as unknown[];
        setGrns(list.map(toHistoryGRN));
        setTotal(matchingTotal(resp, list.length));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [storeId, refreshKey, attempt]);

  // The supplier block of the print: the receipt names its supplier; the
  // address and GSTIN are the supplier's own record (fail-soft: name only).
  useEffect(() => {
    setVendor(null);
    if (!printGrn?.vendor_id) return;
    let cancelled = false;
    vendorsApi
      .getVendor(printGrn.vendor_id)
      .then((v: Record<string, unknown>) => {
        if (cancelled || !v) return;
        const address = [v.address, v.city, v.state].filter((x) => typeof x === 'string' && x.trim()).join(', ');
        setVendor({ address, gstin: typeof v.gstin === 'string' ? v.gstin : '' });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [printGrn]);

  // The GRN prints the shop that RECEIVED it (on all stores the list mixes shops).
  useEffect(() => {
    const sid = printGrn?.store_id || storeId;
    if (!printGrn || !sid) {
      setIdentity(null);
      return;
    }
    let cancelled = false;
    resolveStoreIdentity(sid)
      .then((id) => !cancelled && setIdentity(id))
      .catch(() => !cancelled && setIdentity(null));
    return () => {
      cancelled = true;
    };
  }, [printGrn, storeId]);

  // Rejected or damaged pieces only. A line's ordered_qty is the WHOLE
  // order's quantity, so "short" against it would flag every split delivery.
  const problem = (g: HistoryGRN) => g.total_rejected > 0 || g.total_received !== g.total_accepted;
  const shown = problemsOnly ? grns.filter(problem) : grns;
  const sv = identity?.store;

  return (
    <div className="space-y-3" data-testid="receipt-history">
      {printGrn && (
        <GRNPrint
          grn={{
            grn_id: printGrn.id,
            grn_number: printGrn.grn_number,
            grn_date: printGrn.received_at,
            po_number: printGrn.po_number,
            vendor_id: printGrn.vendor_id || '',
            vendor_name: printGrn.vendor_name || '',
            vendor_address: vendor?.address || '',
            vendor_gstin: vendor?.gstin || '',
            items: printGrn.items.map((i) => {
              // No order behind the line (bought without PO): nothing was ordered to differ from.
              const ordered = typeof i.ordered_qty === 'number' ? i.ordered_qty : i.received_qty;
              return {
                product_id: i.product_id,
                product_name: i.product_name ?? i.product_id,
                hsn_code: undefined,
                ordered_qty: ordered,
                received_qty: i.received_qty,
                variance: i.received_qty - ordered,
                remarks: i.rejection_reason,
              };
            }),
            quality_inspection:
              printGrn.quality === 'passed' ? 'accepted' : printGrn.quality === 'failed' ? 'rejected' : 'partially_accepted',
            inspection_remarks: undefined,
          }}
          store={{
            storeName: sv?.storeName || sv?.storeCode || '',
            storeCode: sv?.storeCode || '',
            brand: sv?.brand || '',
            address: sv?.address || '',
            city: sv?.city || '',
            state: sv?.state || '',
            stateCode: sv?.stateCode || '',
            pincode: sv?.pincode || '',
            phone: (sv as { phone?: string } | undefined)?.phone,
            gstin: sv?.gstin as string | undefined,
          }}
          entity={identity?.entity ?? null}
          onClose={() => setPrintGrn(null)}
        />
      )}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* The list is the server's newest page: when it is a cut, say so. */}
        {grns.length < total ? (
          <p className="text-xs" style={{ color: 'var(--ink-4)' }} data-testid="grn-list-cut">
            Latest {grns.length} of {total} receipts, newest first.
          </p>
        ) : (
          <span />
        )}
        <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: 'var(--ink-3)' }}>
          <input type="checkbox" checked={problemsOnly} onChange={(e) => setProblemsOnly(e.target.checked)} />
          Only with damaged or rejected pieces
        </label>
      </div>
      {failed ? (
        <div className="card text-center py-8 mb-4" role="alert" style={{ color: 'var(--err)' }}>
        <p className="text-sm">Could not load the receipt history.</p>
        <button type="button" className="btn sm mt-2" onClick={() => setAttempt((n) => n + 1)}>Retry</button>
      </div>
      ) : shown.length === 0 ? (
        <div className="card text-center py-10" style={{ color: 'var(--ink-4)' }}>
          <FileText className="w-8 h-8 mx-auto mb-2" style={{ color: 'var(--ink-5)' }} />
          {!problemsOnly
            ? 'No goods receipts yet.'
            : grns.length < total
              ? `No receipts with damaged or rejected pieces in the latest ${grns.length} receipts.`
              : 'No receipts with damaged or rejected pieces.'}
        </div>
      ) : (
        shown.map((g) => {
          const rejectedItems = g.items.filter((i) => (i.rejected_qty || 0) > 0);
          return (
            <div key={g.id} className="card" style={{ padding: 16 }}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold mono" style={{ color: 'var(--ink)' }}>{g.grn_number}</p>
                  <p className="text-sm" style={{ color: 'var(--ink-4)' }}>{g.source}</p>
                  {!storeId && <RowShop storeId={g.store_id} />}
                </div>
                <div className="flex items-center gap-2">
                  <span className={clsx('chip', CHIP[g.quality])}>{WORD[g.quality]}</span>
                  <button type="button" className="btn sm" onClick={() => setPrintGrn(g)} title="Print GRN">
                    <Printer className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              <p className="text-sm mt-2" style={{ color: 'var(--ink-3)' }}>
                {g.received_at ? new Date(g.received_at).toLocaleDateString('en-IN') : '—'} · received {g.total_received} ·
                into stock {g.total_accepted} · rejected {g.total_rejected} · by {g.received_by}
              </p>
              {rejectedItems.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {rejectedItems.map((i, idx) => (
                    <li key={idx} className="text-xs" style={{ color: 'var(--err)' }}>
                      {i.product_name || i.product_id}: {i.rejected_qty} rejected
                      {i.rejection_reason ? ` — ${i.rejection_reason}` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
