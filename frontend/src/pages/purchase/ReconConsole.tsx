// ============================================================================
// IMS 2.0 - Accountant Reconciliation Console (Purchase S6)
// ============================================================================
// Allows an ACCOUNTANT / ADMIN / SUPERADMIN to:
//   1. Tick the 4 reconciliation flags on each vendor purchase invoice
//      (Reconciled / Entered in Tally / Filed GST / Payment settled).
//   2. View 4 accountant worklists:
//      - Stock yet to receive (open POs with unreceived lines)
//      - Vendor returns (open / in-flight)
//      - Pending scheme credit notes (VOLUME_REBATE CNs not yet received)
//      - Pending return credit notes (return CNs not yet issued)
//
// Route: /purchase/recon-console
// Roles: ACCOUNTANT / ADMIN / SUPERADMIN
//
// API calls use purchaseRecon.ts and purchaseInvoicesApi from vendorAp.ts
// -- imported DIRECTLY (not via the barrel) to avoid TS2614.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckSquare, Square, Loader2, RefreshCw, ChevronDown, ChevronRight,
  Package, RotateCcw, FileText, AlertTriangle, ClipboardCheck,
} from 'lucide-react';
import { purchaseReconApi, type ReconBlock, type ReconWorklists } from '../../services/api/purchaseRecon';
import { purchaseInvoicesApi, type PurchaseInvoice } from '../../services/api/vendorAp';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// ---- helpers ---------------------------------------------------------------

const inr = (n?: number) =>
  n == null ? '-' : `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

function fmtDate(iso?: string) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtDateTime(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 16);
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

type ReconFlag = 'reconciled' | 'entered_tally' | 'filed_gst' | 'payment_settled';

const FLAG_LABELS: Record<ReconFlag, string> = {
  reconciled: 'Reconciled',
  entered_tally: 'Tally',
  filed_gst: 'GST Filed',
  payment_settled: 'Paid',
};

const FLAG_TITLES: Record<ReconFlag, string> = {
  reconciled: 'Physically reconciled with vendor statement',
  entered_tally: 'Entered into Tally',
  filed_gst: 'Included in GST return (GSTR-2B / GSTR-3B)',
  payment_settled: 'Payment scheduled or made',
};

const FLAGS: ReconFlag[] = ['reconciled', 'entered_tally', 'filed_gst', 'payment_settled'];

// ---- Recon Checkbox --------------------------------------------------------

interface ReconCheckboxProps {
  flag: ReconFlag;
  recon: ReconBlock;
  invoiceId: string;
  saving: boolean;
  onToggle: (flag: ReconFlag, value: boolean) => void;
}

function ReconCheckbox({ flag, recon, saving, onToggle }: ReconCheckboxProps) {
  const checked = !!recon[flag];
  const by = recon[`${flag}_by` as keyof ReconBlock] as string | undefined;
  const at = recon[`${flag}_at` as keyof ReconBlock] as string | undefined;

  const tooltip = checked && (by || at)
    ? `${by ? `By: ${by}` : ''}${by && at ? '\n' : ''}${at ? `At: ${fmtDateTime(at)}` : ''}`
    : FLAG_TITLES[flag];

  return (
    <div className="flex flex-col items-center gap-0.5">
      <button
        type="button"
        onClick={() => !saving && onToggle(flag, !checked)}
        disabled={saving}
        title={tooltip}
        aria-label={`${FLAG_LABELS[flag]}: ${checked ? 'ticked' : 'unticked'}`}
        className={[
          'flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors',
          saving ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:opacity-80',
          checked
            ? 'bg-green-100 text-green-800 border border-green-300'
            : 'bg-gray-50 text-gray-500 border border-gray-200 hover:border-gray-400',
        ].join(' ')}
      >
        {checked
          ? <CheckSquare size={13} className="text-green-600 shrink-0" />
          : <Square size={13} className="text-gray-400 shrink-0" />}
        <span>{FLAG_LABELS[flag]}</span>
      </button>
      {checked && at && (
        <span className="text-[10px] text-gray-400 text-center leading-tight">
          {fmtDate(at)}
        </span>
      )}
    </div>
  );
}

// ---- Bills list with recon ticks -------------------------------------------

interface BillRowProps {
  invoice: PurchaseInvoice;
  recon: ReconBlock;
  saving: boolean;
  expanded: boolean;
  onToggle: (flag: ReconFlag, value: boolean) => void;
  onExpand: () => void;
}

function BillRow({ invoice, recon, saving, expanded, onToggle, onExpand }: BillRowProps) {
  const allDone = FLAGS.every((f) => !!recon[f]);
  const donePct = Math.round((FLAGS.filter((f) => !!recon[f]).length / FLAGS.length) * 100);

  return (
    <div className={[
      'card mb-2 overflow-hidden transition-shadow',
      allDone ? 'border-l-4 border-l-green-400' : 'border-l-4 border-l-amber-300',
    ].join(' ')}>
      {/* Header row */}
      <div className="flex items-start gap-3 p-3">
        {/* Expand toggle */}
        <button
          onClick={onExpand}
          className="mt-1 text-gray-400 hover:text-gray-600"
          aria-label={expanded ? 'Collapse' : 'Expand'}
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>

        {/* Bill info */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="font-semibold text-gray-900 text-sm">
              {invoice.vendor_invoice_no || invoice.vendor_name || '-'}
            </span>
            {invoice.vendor_name && invoice.vendor_invoice_no && (
              <span className="text-gray-500 text-xs">{invoice.vendor_name}</span>
            )}
            <span className="text-gray-400 text-xs">{fmtDate(invoice.vendor_invoice_date)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-0.5">
            <span className="text-gray-700 text-sm font-medium">
              {inr(invoice.total_amount ?? (invoice as { total?: number }).total)}
            </span>
            <span className={[
              'text-xs px-1.5 py-0.5 rounded',
              invoice.status === 'PAID'
                ? 'bg-green-100 text-green-700'
                : invoice.status === 'OUTSTANDING'
                ? 'bg-amber-100 text-amber-700'
                : 'bg-gray-100 text-gray-600',
            ].join(' ')}>
              {invoice.status ?? 'OUTSTANDING'}
            </span>
            {invoice.po_id && (
              <span className="text-xs text-blue-600">PO linked</span>
            )}
          </div>
          {/* Progress bar */}
          <div className="mt-1.5 flex items-center gap-2">
            <div className="flex-1 bg-gray-100 rounded-full h-1.5">
              <div
                className={`h-1.5 rounded-full transition-all ${allDone ? 'bg-green-500' : 'bg-amber-400'}`}
                style={{ width: `${donePct}%` }}
              />
            </div>
            <span className="text-[10px] text-gray-400 shrink-0">{donePct}%</span>
          </div>
        </div>

        {/* 4 recon tick buttons */}
        <div className="flex flex-wrap gap-1 shrink-0">
          {FLAGS.map((flag) => (
            <ReconCheckbox
              key={flag}
              flag={flag}
              recon={recon}
              invoiceId={invoice.purchase_invoice_id}
              saving={saving}
              onToggle={onToggle}
            />
          ))}
          {saving && <Loader2 size={14} className="animate-spin text-gray-400 self-center ml-1" />}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-100 px-4 py-3 bg-gray-50 text-xs text-gray-600 space-y-1">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div><span className="text-gray-400">Invoice #</span><br />{invoice.vendor_invoice_no || '-'}</div>
            <div><span className="text-gray-400">Date</span><br />{fmtDate(invoice.vendor_invoice_date)}</div>
            <div><span className="text-gray-400">Taxable</span><br />{inr(invoice.taxable_amount)}</div>
            <div>
              <span className="text-gray-400">Tax</span><br />
              {invoice.is_interstate
                ? `IGST ${inr(invoice.igst)}`
                : `CGST ${inr(invoice.cgst)} + SGST ${inr(invoice.sgst)}`}
            </div>
          </div>
          {recon.note && (
            <div className="mt-1 p-2 bg-amber-50 border border-amber-200 rounded">
              <span className="text-amber-700 font-medium">Note: </span>
              <span className="text-gray-700">{recon.note}</span>
              {recon.note_at && (
                <span className="text-gray-400 ml-2">({fmtDateTime(recon.note_at)})</span>
              )}
            </div>
          )}
          {recon.last_updated_at && (
            <div className="text-gray-400">
              Last updated: {fmtDateTime(recon.last_updated_at)}
              {recon.last_updated_by ? ` by ${recon.last_updated_by}` : ''}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Worklist panel --------------------------------------------------------

type WorklistTab =
  | 'stock_yet_to_receive'
  | 'vendor_returns'
  | 'pending_credit_notes_scheme'
  | 'pending_credit_notes_return';

const WORKLIST_TABS: { id: WorklistTab; label: string; icon: typeof Package }[] = [
  { id: 'stock_yet_to_receive', label: 'Stock to Receive', icon: Package },
  { id: 'vendor_returns', label: 'Vendor Returns', icon: RotateCcw },
  { id: 'pending_credit_notes_scheme', label: 'Scheme CNs', icon: FileText },
  { id: 'pending_credit_notes_return', label: 'Return CNs', icon: ClipboardCheck },
];

interface WorklistsProps {
  lists: ReconWorklists;
  onMarkCnReceived: (creditNoteNumber: string) => void | Promise<void>;
}

function Worklists({ lists, onMarkCnReceived }: WorklistsProps) {
  const [activeTab, setActiveTab] = useState<WorklistTab>('stock_yet_to_receive');

  const counts: Record<WorklistTab, number> = {
    stock_yet_to_receive: lists.stock_yet_to_receive.length,
    vendor_returns: lists.vendor_returns.length,
    pending_credit_notes_scheme: lists.pending_credit_notes_scheme.length,
    pending_credit_notes_return: lists.pending_credit_notes_return.length,
  };

  return (
    <div className="card">
      <div className="px-4 pt-4 pb-2 border-b border-gray-100">
        <h2 className="font-semibold text-gray-800 mb-3">Worklists</h2>
        <div className="flex flex-wrap gap-1">
          {WORKLIST_TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={[
                'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors',
                activeTab === id
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
              ].join(' ')}
            >
              <Icon size={13} />
              {label}
              {counts[id] > 0 && (
                <span className={[
                  'ml-1 text-xs px-1.5 py-0.5 rounded-full',
                  activeTab === id ? 'bg-white/20 text-white' : 'bg-amber-200 text-amber-800',
                ].join(' ')}>
                  {counts[id]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {activeTab === 'stock_yet_to_receive' && (
          <StockYetToReceiveTable rows={lists.stock_yet_to_receive} />
        )}
        {activeTab === 'vendor_returns' && (
          <VendorReturnsTable rows={lists.vendor_returns} />
        )}
        {activeTab === 'pending_credit_notes_scheme' && (
          <SchemeCnTable
            rows={lists.pending_credit_notes_scheme}
            onMarkReceived={onMarkCnReceived}
          />
        )}
        {activeTab === 'pending_credit_notes_return' && (
          <ReturnCnTable rows={lists.pending_credit_notes_return} />
        )}
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-10 text-gray-400">
      <CheckSquare size={28} className="mx-auto mb-2 text-green-400" />
      <p className="text-sm">{message}</p>
    </div>
  );
}

function StockYetToReceiveTable({ rows }: { rows: ReconWorklists['stock_yet_to_receive'] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (!rows.length) return <EmptyState message="Nothing pending — all PO lines received." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">PO #</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium">Expected</th>
            <th className="pb-2 pr-4 font-medium text-right">Pending Qty</th>
            <th className="pb-2 font-medium">Lines</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = row.po_id ?? String(i);
            const isOpen = expanded.has(key);
            return (
              <>
                <tr key={key} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2 pr-4">
                    <span className="font-medium text-gray-800">{row.po_number ?? row.po_id ?? '-'}</span>
                  </td>
                  <td className="py-2 pr-4">
                    <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                      {row.status ?? '-'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-500">{fmtDate(row.expected_date)}</td>
                  <td className="py-2 pr-4 text-right font-medium text-amber-700">
                    {row.total_pending_qty}
                  </td>
                  <td className="py-2">
                    <button
                      onClick={() => {
                        const next = new Set(expanded);
                        if (isOpen) next.delete(key); else next.add(key);
                        setExpanded(next);
                      }}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      {isOpen ? 'Hide' : `View ${row.open_lines.length} line${row.open_lines.length !== 1 ? 's' : ''}`}
                    </button>
                  </td>
                </tr>
                {isOpen && (
                  <tr key={`${key}-lines`}>
                    <td colSpan={5} className="pb-2 pt-0">
                      <div className="bg-gray-50 rounded p-2 text-xs text-gray-600">
                        <table className="w-full">
                          <thead>
                            <tr className="text-gray-400">
                              <th className="text-left pb-1 font-medium">Product</th>
                              <th className="text-right pb-1 font-medium">Ordered</th>
                              <th className="text-right pb-1 font-medium">Received</th>
                              <th className="text-right pb-1 font-medium text-amber-700">Pending</th>
                            </tr>
                          </thead>
                          <tbody>
                            {row.open_lines.map((ln, j) => (
                              <tr key={j} className="border-t border-gray-100">
                                <td className="py-1">{ln.product_name ?? ln.product_id ?? '-'}</td>
                                <td className="py-1 text-right">{ln.ordered_qty}</td>
                                <td className="py-1 text-right">{ln.received_qty}</td>
                                <td className="py-1 text-right text-amber-700 font-medium">{ln.pending_qty}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VendorReturnsTable({ rows }: { rows: ReconWorklists['vendor_returns'] }) {
  if (!rows.length) return <EmptyState message="Nothing pending — no open vendor returns." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">Return #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Type</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium text-right">Value</th>
            <th className="pb-2 font-medium">CN #</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.return_id ?? i} className="border-b border-gray-50 hover:bg-gray-50">
              <td className="py-2 pr-4 font-medium text-gray-800">{row.return_id ?? '-'}</td>
              <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                  {row.return_type ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                  {row.status ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4 text-right">{inr(row.total_value)}</td>
              <td className="py-2 text-gray-500 text-xs">{row.credit_note_number ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SchemeCnTable({
  rows,
  onMarkReceived,
}: {
  rows: ReconWorklists['pending_credit_notes_scheme'];
  onMarkReceived: (creditNoteNumber: string) => void | Promise<void>;
}) {
  const [saving, setSaving] = useState<Set<string>>(new Set());
  if (!rows.length) return <EmptyState message="Nothing pending — all scheme credit notes received." />;

  const mark = async (cn: string) => {
    setSaving((prev) => new Set([...prev, cn]));
    try {
      await onMarkReceived(cn);
    } finally {
      setSaving((prev) => {
        const next = new Set(prev);
        next.delete(cn);
        return next;
      });
    }
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">CN #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Rebate #</th>
            <th className="pb-2 pr-4 font-medium text-right">Amount</th>
            <th className="pb-2 pr-4 font-medium">Raised</th>
            <th className="pb-2 font-medium text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const cn = row.credit_note_number;
            const busy = cn ? saving.has(cn) : false;
            return (
              <tr key={cn ?? i} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2 pr-4 font-medium text-gray-800">{cn ?? '-'}</td>
                <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
                <td className="py-2 pr-4 text-gray-500 text-xs">{row.rebate_id ?? '-'}</td>
                <td className="py-2 pr-4 text-right text-green-700 font-medium">
                  {row.amount != null ? inr(row.amount) : row.amount_paise != null ? inr(row.amount_paise / 100) : '-'}
                </td>
                <td className="py-2 pr-4 text-gray-500">{fmtDate(row.created_at)}</td>
                <td className="py-2 text-right">
                  {cn ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void mark(cn)}
                      className="text-xs font-medium px-2.5 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      title="Mark this scheme credit note as physically received"
                    >
                      {busy ? 'Saving…' : 'Mark received'}
                    </button>
                  ) : (
                    <span className="text-xs text-gray-400">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReturnCnTable({ rows }: { rows: ReconWorklists['pending_credit_notes_return'] }) {
  if (!rows.length) return <EmptyState message="Nothing pending — all return credit notes issued." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">Return #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium text-right">Value</th>
            <th className="pb-2 font-medium">CN #</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.return_id ?? i} className="border-b border-gray-50 hover:bg-gray-50">
              <td className="py-2 pr-4 font-medium text-gray-800">{row.return_id ?? '-'}</td>
              <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                  {row.status ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4 text-right">{inr(row.total_value)}</td>
              <td className="py-2 text-gray-500 text-xs">{row.credit_note_number ?? 'Not issued'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- Main page -------------------------------------------------------------

const DEFAULT_RECON: ReconBlock = {
  reconciled: false,
  entered_tally: false,
  filed_gst: false,
  payment_settled: false,
};

export default function ReconConsole() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId;

  // Bills list + their recon blocks
  const [invoices, setInvoices] = useState<PurchaseInvoice[]>([]);
  const [reconMap, setReconMap] = useState<Record<string, ReconBlock>>({});
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [loadingBills, setLoadingBills] = useState(true);

  // Worklists
  const [worklists, setWorklists] = useState<ReconWorklists>({
    stock_yet_to_receive: [],
    vendor_returns: [],
    pending_credit_notes_scheme: [],
    pending_credit_notes_return: [],
  });
  const [loadingWorklists, setLoadingWorklists] = useState(true);

  // Filter state
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchText, setSearchText] = useState('');

  // ---- Load invoices -------------------------------------------------------

  const loadInvoices = useCallback(async () => {
    setLoadingBills(true);
    try {
      const { purchase_invoices: rows } = await purchaseInvoicesApi.list({
        ...(storeId ? { store_id: storeId } : {}),
        ...(statusFilter ? { status: statusFilter } : {}),
      });
      setInvoices(rows);
      // Bulk-fetch recon blocks. Fire them concurrently; each is fail-soft.
      const blocks = await Promise.all(
        rows.map((inv) => purchaseReconApi.getRecon(inv.purchase_invoice_id ?? inv.bill_id ?? ''))
      );
      const map: Record<string, ReconBlock> = {};
      rows.forEach((inv, i) => {
        const id = inv.purchase_invoice_id ?? inv.bill_id ?? '';
        map[id] = blocks[i]?.recon ?? { ...DEFAULT_RECON };
      });
      setReconMap(map);
    } catch (err) {
      toast.error('Failed to load purchase invoices');
      console.error(err);
    } finally {
      setLoadingBills(false);
    }
  }, [storeId, statusFilter, toast]);

  // ---- Load worklists ------------------------------------------------------

  const loadWorklists = useCallback(async () => {
    setLoadingWorklists(true);
    const data = await purchaseReconApi.getWorklists(
      storeId ? { store_id: storeId } : undefined
    );
    setWorklists(data);
    setLoadingWorklists(false);
  }, [storeId]);

  useEffect(() => { void loadInvoices(); }, [loadInvoices]);
  useEffect(() => { void loadWorklists(); }, [loadWorklists]);

  // ---- Handle tick toggle --------------------------------------------------

  const handleToggle = useCallback(async (invoiceId: string, flag: ReconFlag, value: boolean) => {
    // Optimistic update
    setReconMap((prev) => ({
      ...prev,
      [invoiceId]: { ...(prev[invoiceId] ?? DEFAULT_RECON), [flag]: value },
    }));
    setSavingIds((prev) => new Set([...prev, invoiceId]));
    try {
      const result = await purchaseReconApi.upsertRecon(invoiceId, { [flag]: value });
      setReconMap((prev) => ({ ...prev, [invoiceId]: result.recon }));
      toast.success(`${FLAG_LABELS[flag]} ${value ? 'ticked' : 'unticked'}`);
    } catch (err) {
      // Rollback on failure
      setReconMap((prev) => ({
        ...prev,
        [invoiceId]: { ...(prev[invoiceId] ?? DEFAULT_RECON), [flag]: !value },
      }));
      toast.error(`Failed to update ${FLAG_LABELS[flag]}`);
      console.error(err);
    } finally {
      setSavingIds((prev) => {
        const next = new Set(prev);
        next.delete(invoiceId);
        return next;
      });
    }
  }, [toast]);

  // ---- Mark a scheme credit-note received ----------------------------------

  const handleMarkCnReceived = useCallback(async (creditNoteNumber: string) => {
    try {
      await purchaseReconApi.markSchemeCnReceived(creditNoteNumber);
      // Optimistically drop it from the pending-scheme worklist (the backend
      // now stamps cn_received_at, so a refetch would exclude it anyway).
      setWorklists((prev) => ({
        ...prev,
        pending_credit_notes_scheme: prev.pending_credit_notes_scheme.filter(
          (r) => r.credit_note_number !== creditNoteNumber,
        ),
      }));
      toast.success(`Credit note ${creditNoteNumber} marked received`);
    } catch (err) {
      toast.error(`Failed to mark ${creditNoteNumber} received`);
      console.error(err);
    }
  }, [toast]);

  // ---- Filtered invoice list -----------------------------------------------

  const filteredInvoices = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return invoices.filter((inv) => {
      if (!q) return true;
      return (
        (inv.vendor_name ?? '').toLowerCase().includes(q) ||
        (inv.vendor_invoice_no ?? '').toLowerCase().includes(q) ||
        (inv.vendor_id ?? '').toLowerCase().includes(q)
      );
    });
  }, [invoices, searchText]);

  // ---- Recon summary badge -------------------------------------------------
  const allDoneCount = useMemo(
    () => invoices.filter((inv) => {
      const id = inv.purchase_invoice_id ?? inv.bill_id ?? '';
      return FLAGS.every((f) => !!(reconMap[id]?.[f]));
    }).length,
    [invoices, reconMap]
  );

  // ---- Render --------------------------------------------------------------

  return (
    <div className="p-4 max-w-6xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Recon Console</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Accountant reconciliation — tick 4 flags per purchase invoice
          </p>
        </div>
        <div className="flex items-center gap-2">
          {invoices.length > 0 && (
            <span className="text-xs text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
              {allDoneCount}/{invoices.length} fully reconciled
            </span>
          )}
          <button
            onClick={() => { void loadInvoices(); void loadWorklists(); }}
            className="btn-primary flex items-center gap-1.5 text-sm"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </div>

      {/* Alert if there are any pending worklist items */}
      {!loadingWorklists && (
        worklists.stock_yet_to_receive.length > 0 ||
        worklists.vendor_returns.length > 0 ||
        worklists.pending_credit_notes_scheme.length > 0 ||
        worklists.pending_credit_notes_return.length > 0
      ) && (
        <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
          <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-500" />
          <span>
            You have pending items in the worklists below —
            {worklists.stock_yet_to_receive.length > 0 && ` ${worklists.stock_yet_to_receive.length} PO(s) with unreceived stock,`}
            {worklists.vendor_returns.length > 0 && ` ${worklists.vendor_returns.length} open vendor return(s),`}
            {worklists.pending_credit_notes_scheme.length > 0 && ` ${worklists.pending_credit_notes_scheme.length} scheme CN(s) pending,`}
            {worklists.pending_credit_notes_return.length > 0 && ` ${worklists.pending_credit_notes_return.length} return CN(s) not issued.`}
          </span>
        </div>
      )}

      {/* ---- Bills section ---- */}
      <section>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <h2 className="font-semibold text-gray-800 text-base">Purchase Invoices</h2>
          <input
            type="text"
            placeholder="Search vendor or invoice #..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="input-field text-sm py-1.5 px-3 w-56"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input-field text-sm py-1.5 px-3"
          >
            <option value="">All statuses</option>
            <option value="OUTSTANDING">Outstanding</option>
            <option value="PARTIAL">Partial</option>
            <option value="PAID">Paid</option>
          </select>
        </div>

        {loadingBills ? (
          <div className="flex items-center gap-2 py-10 justify-center text-gray-400">
            <Loader2 size={20} className="animate-spin" />
            <span className="text-sm">Loading invoices...</span>
          </div>
        ) : filteredInvoices.length === 0 ? (
          <div className="card p-8 text-center text-gray-400">
            <FileText size={28} className="mx-auto mb-2" />
            <p className="text-sm">
              {invoices.length === 0
                ? 'No purchase invoices found for this store.'
                : 'No invoices match your search.'}
            </p>
          </div>
        ) : (
          <div>
            {filteredInvoices.map((inv) => {
              const id = inv.purchase_invoice_id ?? inv.bill_id ?? '';
              return (
                <BillRow
                  key={id}
                  invoice={inv}
                  recon={reconMap[id] ?? { ...DEFAULT_RECON }}
                  saving={savingIds.has(id)}
                  expanded={expandedIds.has(id)}
                  onToggle={(flag, value) => handleToggle(id, flag, value)}
                  onExpand={() => {
                    setExpandedIds((prev) => {
                      const next = new Set(prev);
                      if (next.has(id)) next.delete(id); else next.add(id);
                      return next;
                    });
                  }}
                />
              );
            })}
          </div>
        )}
      </section>

      {/* ---- Worklists section ---- */}
      <section>
        {loadingWorklists ? (
          <div className="card p-8 flex items-center gap-2 justify-center text-gray-400">
            <Loader2 size={20} className="animate-spin" />
            <span className="text-sm">Loading worklists...</span>
          </div>
        ) : (
          <Worklists lists={worklists} onMarkCnReceived={handleMarkCnReceived} />
        )}
      </section>
    </div>
  );
}
