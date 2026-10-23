// ============================================================================
// IMS 2.0 - "Receipts still waiting" (the receiving screen's recovery panel)
// ============================================================================
// A created-but-unaccepted receipt adds NO stock and its order stays
// receivable, which used to read as "nothing happened". A partly accepted one
// holds lines back (for the catalogue, or beyond its order for the manager).
// Each row offers the one-press way out: Add to stock / Not received / Void.

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import { vendorsApi } from '../../services/api/inventory';
import { useToast } from '../../context/ToastContext';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { heldLinesSummary, reportGrnAccept } from './grnAcceptToast';

interface WaitingRow {
  grn_id: string;
  grn_number: string;
  vendor_name?: string;
  vendor_invoice_no?: string;
  created_at?: string;
  lines: number;
  status: string;
  held: string;
  overOrder: number;
}

interface PendingReceiptsProps {
  storeId: string;
  /** Bumped by the page after a receipt is posted, to re-read the list. */
  refreshKey: number;
  highlight: string | null;
  /** A receipt finished from here: its labels, and the order list re-read. */
  onAccepted: (grnId: string, grnNumber: string) => void;
  onChanged: () => void;
}

export function PendingReceipts({ storeId, refreshKey, highlight, onAccepted, onChanged }: PendingReceiptsProps) {
  const toast = useToast();
  const [rows, setRows] = useState<WaitingRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const scope = { store_id: storeId || undefined };
      const [pend, part] = await Promise.all([
        vendorsApi.getGRNs({ ...scope, status: 'PENDING' }),
        vendorsApi.getGRNs({ ...scope, status: 'PARTIALLY_ACCEPTED' }),
      ]);
      const collect = (res: Record<string, unknown>) => {
        const list = (res?.grns || res?.items || res || []) as unknown;
        return (Array.isArray(list) ? list : []) as Array<Record<string, unknown>>;
      };
      setRows(
        [...collect(pend), ...collect(part)].map((g) => {
          const held = heldLinesSummary(g.unresolved_lines);
          return {
            grn_id: String(g.grn_id || g.id || ''),
            grn_number: String(g.grn_number || ''),
            vendor_name: g.vendor_name ? String(g.vendor_name) : undefined,
            vendor_invoice_no: g.vendor_invoice_no ? String(g.vendor_invoice_no) : undefined,
            created_at: g.created_at ? String(g.created_at) : undefined,
            lines: Array.isArray(g.items) ? g.items.length : 0,
            status: String(g.status || 'PENDING'),
            held: held.text,
            overOrder: held.over,
          };
        }),
      );
    } catch {
      /* fail-soft: the panel just doesn't render */
    }
  }, [storeId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const run = async (grnId: string, work: () => Promise<void>, failText: string) => {
    setBusy(grnId);
    try {
      await work();
      await load();
      onChanged();
    } catch (err) {
      // These recovery messages are long and load-bearing ("the units already
      // received are safe and will not be counted twice"): give them time.
      toast.error(err instanceof Error ? err.message : failText, 20000);
    } finally {
      setBusy(null);
    }
  };

  const accept = (g: WaitingRow) =>
    run(g.grn_id, async () => {
      const res = await vendorsApi.acceptGRN(g.grn_id);
      reportGrnAccept(toast, g.grn_number, res);
      onAccepted(g.grn_id, g.grn_number);
    }, `Failed to accept GRN ${g.grn_number}`);

  const notReceived = (g: WaitingRow) => {
    if (
      !window.confirm(
        `Mark the units ${g.grn_number} holds beyond its order as NOT received? ` +
          'Use this when the vendor did not send them (a second receipt of the same box, say). ' +
          'Units the order still wanted go on the shelf; anything already on the shelf stays.',
      )
    )
      return;
    void run(g.grn_id, async () => {
      const res = await vendorsApi.dropOverOrder(g.grn_id);
      toast.success(
        `GRN ${g.grn_number}: ${res.dropped_units} unit(s) marked not received` +
          (res.units_added ? `; ${res.units_added} the order still wanted added to stock` : ''),
      );
    }, `Failed to update GRN ${g.grn_number}`);
  };

  const voidIt = (g: WaitingRow) => {
    // Never promise "no stock was added": a receipt killed mid-accept sits at
    // PENDING with real units on the shelf. The server refuses that void (409).
    if (
      !window.confirm(
        `Void ${g.grn_number}? Use this only for a duplicate or mistake that never put stock on the shelf. ` +
          'If an earlier acceptance was interrupted, this will be refused — accept it again instead.',
      )
    )
      return;
    void run(g.grn_id, async () => {
      await vendorsApi.voidGRN(g.grn_id);
      toast.success(`GRN ${g.grn_number} voided`);
    }, `Failed to void GRN ${g.grn_number}`);
  };

  if (rows.length === 0) return null;

  return (
    <div className="card border-amber-200 bg-amber-50/40 mb-4" style={{ padding: 16 }} data-testid="receipts-waiting">
      <div className="flex items-center gap-2 mb-2">
        <AlertCircle className="w-4 h-4 text-amber-600" />
        <h3 className="font-semibold text-gray-900">Receipts still waiting ({rows.length})</h3>
      </div>
      <p className="text-xs text-gray-600 mb-3">
        A <strong>pending</strong> receipt was saved but never added to stock, so its order still shows as
        due — add the right one to stock and void duplicates. A <strong>partly accepted</strong> one is
        holding lines back: a line waiting to be catalogued goes on the shelf by itself once the product is
        finished ("Add to stock" tries again now); a line beyond what the order asked for waits for you —
        "Not received" if the vendor sent nothing extra, "Add to stock" if they did.
      </p>
      <div className="space-y-2">
        {rows.map((g) => (
          <div
            key={g.grn_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2"
            style={highlight && highlight === g.grn_number ? { boxShadow: '0 0 0 2px var(--warn)' } : undefined}
          >
            <div className="min-w-0 text-sm">
              <span className="font-medium text-gray-900">{g.grn_number}</span>{' '}
              <PurchaseStatusChip status={g.status} kind="grn" />
              {g.vendor_name && <span className="text-gray-500"> · {g.vendor_name}</span>}
              {g.status === 'PARTIALLY_ACCEPTED' && <span className="text-amber-700"> · {g.held}</span>}
              {g.vendor_invoice_no && <span className="text-gray-500"> · Bill {g.vendor_invoice_no}</span>}
              {g.created_at && <span className="text-gray-400"> · {g.created_at.slice(0, 10)}</span>}
              <span className="text-gray-500"> · {g.lines} line(s)</span>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <button type="button" className="btn sm accent" disabled={busy === g.grn_id} onClick={() => accept(g)}>
                {busy === g.grn_id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                Add to stock
              </button>
              {g.overOrder > 0 && (
                <button type="button" className="btn sm" disabled={busy === g.grn_id} onClick={() => notReceived(g)}>
                  <X className="w-3.5 h-3.5" /> Not received
                </button>
              )}
              {/* A held receipt can be voided while none of it is in stock;
                  the server refuses one that put units on the shelf. */}
              <button type="button" className="btn sm" disabled={busy === g.grn_id} onClick={() => voidIt(g)}>
                <X className="w-3.5 h-3.5" /> Void (duplicate)
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
