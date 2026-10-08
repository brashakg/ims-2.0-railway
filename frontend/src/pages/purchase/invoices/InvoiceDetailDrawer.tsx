// ============================================================================
// Purchase Invoices - 3-way-match badge, config note, comparison table and
// the detail drawer. MOVED verbatim out of ../PurchaseInvoicesTab.tsx (Wave 6).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  X, Loader2, FileText, AlertTriangle, CheckCircle2, ShieldCheck, Scale, ShieldAlert,
} from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceMatch,
  type PurchaseInvoiceConfig,
  type PurchaseConfig,
  type MatchStatus,
  type MatchLine,
} from '../../../services/api/vendorAp';
import { useToast } from '../../../context/ToastContext';
import { useAuth } from '../../../context/AuthContext';
import { byPerson } from '../purchaseTypes';
import { inr, errMsg, APPROVE_ROLES } from './shared';

// ============================================================================
// Phase 2 - 3-way match: badge + valuation/tolerance note + detail drawer
// ============================================================================

// Visual config for each match verdict. MATCHED = the invoice agrees with the
// PO + GRN within tolerance; ON_HOLD_EXCEPTION = a line is outside tolerance and
// needs a human; MATCHED_OVERRIDE = a reviewer approved despite the variance.
const MATCH_BADGE: Record<MatchStatus, { label: string; color: string; Icon: typeof CheckCircle2 }> = {
  MATCHED: { label: 'Matched', color: 'bg-green-100 text-green-800', Icon: CheckCircle2 },
  ON_HOLD_EXCEPTION: { label: 'On hold', color: 'bg-amber-100 text-amber-800', Icon: AlertTriangle },
  MATCHED_OVERRIDE: { label: 'Override approved', color: 'bg-blue-100 text-blue-800', Icon: ShieldCheck },
  UNMATCHED: { label: 'No PO/GRN', color: 'bg-gray-100 text-gray-600', Icon: FileText },
  NOT_APPLICABLE: { label: 'Not matched', color: 'bg-gray-100 text-gray-500', Icon: FileText },
};

export function MatchBadge({ status }: { status: MatchStatus }) {
  const cfg = MATCH_BADGE[status] ?? MATCH_BADGE.NOT_APPLICABLE;
  const { label, color, Icon } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" /> {label}
    </span>
  );
}

// "MOVING_AVERAGE" -> "Moving average" for display.
function methodLabel(method?: string): string {
  if (!method) return '';
  return method
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(' ');
}

// The active config values live under `.config` (GET /config returns
// { config, defaults, valuation_methods }).
function activeConfig(config: PurchaseInvoiceConfig | null): PurchaseConfig | undefined {
  return config?.config ?? undefined;
}

// "+/-5%" tolerance label from a percentage, or '' when unknown.
function tolerancePctLabel(pct?: number | null): string {
  return pct == null ? '' : `+/-${pct}%`;
}

// Small read-only note under the page intro: the active valuation method +
// match tolerance, so the user understands how MATCHED / ON_HOLD is decided.
// Renders nothing when the backend exposes no config (fail-soft).
export function ConfigNote({ config }: { config: PurchaseInvoiceConfig | null }) {
  const cfg = activeConfig(config);
  if (!cfg) return null;
  const method = methodLabel(cfg.valuation_method);
  const tol = tolerancePctLabel(cfg.match_tolerance_pct);
  if (!method && !tol) return null;
  return (
    <p className="inline-flex flex-wrap items-center gap-x-1.5 text-xs text-gray-400">
      <Scale className="w-3.5 h-3.5 text-gray-400" />
      {method && <span>Valuation: <span className="font-medium text-gray-500">{method}</span></span>}
      {method && tol && <span className="text-gray-300">·</span>}
      {tol && <span>3-way match tolerance: <span className="font-medium text-gray-500">{tol}</span></span>}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Number formatting helpers for the 3-way table (compact, fail-soft on undef)
// ---------------------------------------------------------------------------
const num = (n?: number) => (n == null || Number.isNaN(n) ? '-' : `${Math.round(n * 100) / 100}`);

// A right-aligned numeric cell that turns amber when its value is part of a
// flagged variance (qty / price out of tolerance).
function VCell({ value, flagged }: { value: string; flagged?: boolean }) {
  return (
    <td className={`px-2 py-1.5 text-right ${flagged ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
      {value}
    </td>
  );
}

// Signed % chip, amber when |pct| exceeds tolerance. Hidden when pct is null
// (not comparable, e.g. the product isn't on the PO).
function VarPct({ pct, tol }: { pct?: number | null; tol?: number }) {
  if (pct == null) return <span className="text-gray-300">-</span>;
  const over = tol != null && Math.abs(pct) > tol;
  const sign = pct > 0 ? '+' : '';
  return (
    <span className={over ? 'text-amber-700 font-semibold' : 'text-gray-500'}>
      {sign}{Math.round(pct * 10) / 10}%
    </span>
  );
}

// The side-by-side 3-way comparison: one row per product line, with PO ordered |
// GRN received | invoice invoiced (qty + price) and the qty/price variance %s +
// reasons. Out-of-tolerance cells are highlighted amber. `tol` is the active
// match tolerance (%) used to flag individual qty/price cells.
function ThreeWayTable({ lines, tol }: { lines: MatchLine[]; tol?: number }) {
  const overTol = (pct?: number | null) => pct != null && tol != null && Math.abs(pct) > tol;
  return (
    <div className="overflow-x-auto border border-gray-200 rounded-lg">
      <table className="w-full text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th rowSpan={2} className="text-left px-2 py-1.5 align-bottom border-r border-gray-200">Item</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Ordered (PO)</th>
            <th colSpan={1} className="text-center px-2 py-1 border-r border-gray-200">Received (GRN)</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Invoiced (Invoice)</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Variance</th>
            <th rowSpan={2} className="text-left px-2 py-1.5 align-bottom">Reasons</th>
          </tr>
          <tr className="text-[11px]">
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Rate</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Qty</th>
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Rate</th>
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Price</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {lines.map((l, i) => {
            const qtyFlag = overTol(l.qty_variance_pct);
            const priceFlag = overTol(l.price_variance_pct);
            return (
              <tr key={l.product_id ? `${l.product_id}-${i}` : i} className="hover:bg-gray-50">
                <td className="px-2 py-1.5 border-r border-gray-200">
                  <div className="font-medium text-gray-900">{l.description || l.product_id || `Line ${i + 1}`}</div>
                  {l.hsn && <div className="text-[11px] text-gray-400">HSN {l.hsn}</div>}
                </td>
                {/* PO */}
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.ordered_qty ?? undefined)}</td>
                <td className="px-2 py-1.5 text-right text-gray-700 border-r border-gray-200">{l.po_unit_price != null ? inr(l.po_unit_price) : '-'}</td>
                {/* GRN */}
                <td className={`px-2 py-1.5 text-right border-r border-gray-200 ${qtyFlag ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
                  {l.received_qty == null ? <span className="text-gray-300">-</span> : num(l.received_qty)}
                </td>
                {/* Invoice */}
                <VCell value={num(l.invoiced_qty)} flagged={qtyFlag} />
                <td className={`px-2 py-1.5 text-right border-r border-gray-200 ${priceFlag ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
                  {l.invoice_unit_price != null ? inr(l.invoice_unit_price) : '-'}
                </td>
                {/* Variance %s */}
                <td className="px-2 py-1.5 text-right"><VarPct pct={l.qty_variance_pct} tol={tol} /></td>
                <td className="px-2 py-1.5 text-right border-r border-gray-200"><VarPct pct={l.price_variance_pct} tol={tol} /></td>
                {/* Reasons */}
                <td className="px-2 py-1.5">
                  {l.reasons && l.reasons.length > 0 ? (
                    <ul className="space-y-0.5">
                      {l.reasons.map((r, ri) => (
                        <li key={ri} className="text-amber-700 flex items-start gap-1">
                          <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" /> <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  ) : l.status === 'EXCEPTION' ? (
                    <span className="text-amber-700">Out of tolerance</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-green-700"><CheckCircle2 className="w-3 h-3" /> OK</span>
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

// ============================================================================
// Detail drawer: header + 3-way match + valuation + approve-exception action
// ============================================================================
export function InvoiceDetailDrawer({
  invoice, config, onClose, onChanged,
}: {
  invoice: PurchaseInvoice;
  config: PurchaseInvoiceConfig | null;
  onClose: () => void;
  onChanged: (updated: Partial<PurchaseInvoice> & { purchase_invoice_id?: string }) => void;
}) {
  const toast = useToast();
  const { hasRole } = useAuth();
  const id = invoice.purchase_invoice_id;
  // Start from the match detail embedded on the list row, then refine with the
  // (possibly recomputed) breakdown from getMatch(). Fail-soft: null -> no match
  // section. `override` is the approval audit, kept locally so the banner updates
  // immediately after an approve without a full reload.
  const [match, setMatch] = useState<PurchaseInvoiceMatch | null>(invoice.match_detail ?? null);
  const [override, setOverride] = useState(invoice.exception_override ?? null);
  const [rowStatus, setRowStatus] = useState<MatchStatus | null>(invoice.match_status ?? null);
  const [loadingMatch, setLoadingMatch] = useState(true);
  const [approving, setApproving] = useState(false);
  const [reason, setReason] = useState('');

  useEffect(() => {
    let alive = true;
    setLoadingMatch(true);
    purchaseInvoicesApi.getMatch(id)
      .then((m) => { if (alive && m) setMatch(m); })
      .finally(() => { if (alive) setLoadingMatch(false); });
    return () => { alive = false; };
  }, [id]);

  // The effective verdict: an approved override wins; else the fetched match
  // verdict; else the row's embedded status; else "not applicable".
  const status: MatchStatus = rowStatus ?? match?.match_status ?? 'NOT_APPLICABLE';
  const onHold = status === 'ON_HOLD_EXCEPTION';
  const canApprove = hasRole(APPROVE_ROLES);

  // Invoice-level exception reasons (the flat `exceptions` list from the match).
  const invoiceReasons = useMemo(() => Array.from(new Set(match?.exceptions ?? [])), [match]);

  const cfg = activeConfig(config);
  const valuationMethod = cfg?.valuation_method;
  // The active match tolerance %, preferring what the match actually used.
  const tol = match?.tolerance_pct ?? cfg?.match_tolerance_pct;
  const matchLines = match?.lines ?? [];
  const summary = match?.summary;
  // Per-line valuation only if a (future) backend stamps unit_cost on the line.
  const valuationLines = (invoice.lines ?? []).filter((l) => l.unit_cost != null || l.valuation_amount != null);

  const approve = async () => {
    if (!reason.trim()) { toast.error('A reason is required to approve the exception'); return; }
    setApproving(true);
    try {
      const res = await purchaseInvoicesApi.approveException(id, { reason: reason.trim() });
      const newStatus = res.match_status ?? 'MATCHED_OVERRIDE';
      setRowStatus(newStatus);
      if (res.exception_override) setOverride(res.exception_override);
      onChanged({ purchase_invoice_id: id, match_status: newStatus, exception_override: res.exception_override });
      toast.success('Exception approved - invoice released for payment');
    } catch (e) {
      toast.error(errMsg(e, 'Failed to approve the exception'));
    } finally {
      setApproving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-white w-full max-w-4xl h-full overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white z-10">
          <div>
            <h3 className="font-semibold text-gray-900 flex items-center gap-2">
              <FileText className="w-5 h-5" /> {invoice.vendor_name || invoice.vendor_id}
              <span className="text-sm font-normal text-gray-500">· {invoice.vendor_invoice_no}</span>
              {invoice.itc_eligible === false && (
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800" title="This bill claims no input credit">No credit</span>
              )}
              {invoice.reverse_charge === true && (
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800" title="You pay this GST to the government; the supplier is owed the taxable value">Reverse charge</span>
              )}
            </h3>
            <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-3">
              <span>{(invoice.vendor_invoice_date || '').slice(0, 10)}</span>
              {invoice.po_number && <span>PO {invoice.po_number}</span>}
              {invoice.grn_number && <span>GRN {invoice.grn_number}</span>}
              <span className="font-medium text-gray-700">{inr(invoice.total_amount)}</span>
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-5 space-y-5">
          {/* Verdict banner */}
          <div className={`rounded-lg border p-3 flex items-start gap-3 ${
            status === 'MATCHED' ? 'bg-green-50 border-green-200'
              : onHold ? 'bg-amber-50 border-amber-200'
              : status === 'MATCHED_OVERRIDE' ? 'bg-blue-50 border-blue-200'
              : 'bg-gray-50 border-gray-200'
          }`}>
            {status === 'MATCHED' ? <CheckCircle2 className="w-5 h-5 text-green-600 mt-0.5" />
              : onHold ? <ShieldAlert className="w-5 h-5 text-amber-600 mt-0.5" />
              : status === 'MATCHED_OVERRIDE' ? <ShieldCheck className="w-5 h-5 text-blue-600 mt-0.5" />
              : <FileText className="w-5 h-5 text-gray-400 mt-0.5" />}
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-gray-900">3-way match</span>
                <MatchBadge status={status} />
              </div>
              <p className="text-xs text-gray-600 mt-1">
                {status === 'MATCHED' && 'Purchase order, goods receipt and supplier invoice agree within tolerance.'}
                {onHold && 'One or more lines are outside tolerance. Review the variances below; an authorised user can approve to release for payment.'}
                {status === 'MATCHED_OVERRIDE' && 'An exception was approved despite a variance.'}
                {status === 'UNMATCHED' && 'This invoice has no linked PO/GRN to match against (manual invoice).'}
                {status === 'NOT_APPLICABLE' && 'No 3-way match has been computed for this invoice.'}
              </p>
              {override && (
                <p className="text-[11px] text-blue-700 mt-1">
                  Override approved{byPerson(override.approved_by_name, override.approved_by)}
                  {override.approved_at ? ` on ${String(override.approved_at).slice(0, 10)}` : ''}
                  {override.reason ? ` - "${override.reason}"` : ''}
                </p>
              )}
              {summary && (summary.total_lines ?? 0) > 0 && (
                <p className="text-[11px] text-gray-500 mt-1">
                  {summary.matched_lines ?? 0} of {summary.total_lines} lines matched
                  {(summary.exception_lines ?? 0) > 0 ? `, ${summary.exception_lines} with exceptions` : ''}
                  {tol != null ? ` · tolerance ${tolerancePctLabel(tol)}` : ''}
                </p>
              )}
            </div>
          </div>

          {/* Invoice-level exception reasons */}
          {invoiceReasons.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-800 mb-1">Why this is on hold</p>
              <ul className="space-y-0.5">
                {invoiceReasons.map((r, i) => (
                  <li key={i} className="text-xs text-amber-700 flex items-start gap-1">
                    <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" /> <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 3-way comparison table */}
          {loadingMatch ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm"><Loader2 className="w-4 h-4 animate-spin" /> Loading 3-way match...</div>
          ) : matchLines.length > 0 ? (
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Line comparison</h4>
              <ThreeWayTable lines={matchLines} tol={tol} />
              <p className="text-[11px] text-gray-400 mt-1.5">
                Qty / price variance is each line&apos;s invoiced value vs the purchase order; amber = outside the
                {tol != null ? ` ${tolerancePctLabel(tol)}` : ''} match tolerance.
              </p>
            </div>
          ) : (
            // No match breakdown -> still show the invoice's own lines so the
            // drawer is never empty (manual invoice / Phase-1-only backend).
            <InvoiceLinesFallback invoice={invoice} />
          )}

          {/* Inventory valuation note (the booking trues up product moving-average
              cost; per-line unit cost is shown only if the backend stamps it). */}
          {(valuationLines.length > 0 || valuationMethod) && (
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1.5">
                <Scale className="w-4 h-4 text-gray-500" /> Inventory valuation
                {valuationMethod && <span className="text-xs font-normal text-gray-400">· {methodLabel(valuationMethod)}</span>}
              </h4>
              {valuationLines.length > 0 ? (
                <div className="overflow-x-auto border border-gray-200 rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 text-gray-500">
                      <tr>
                        <th className="text-left px-2 py-1.5">Item</th>
                        <th className="text-right px-2 py-1.5">Qty</th>
                        <th className="text-right px-2 py-1.5">Unit cost</th>
                        <th className="text-right px-2 py-1.5">Stock value</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {valuationLines.map((l, i) => (
                        <tr key={i} className="hover:bg-gray-50">
                          <td className="px-2 py-1.5 text-gray-900">{l.product_name || l.sku || `Line ${i + 1}`}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">{num(l.quantity)}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">{l.unit_cost != null ? inr(l.unit_cost) : '-'}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">
                            {l.valuation_amount != null ? inr(l.valuation_amount)
                              : l.unit_cost != null ? inr(l.unit_cost * (l.quantity || 0)) : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-xs text-gray-500">
                  Booking this invoice trues up each product&apos;s landed cost
                  {valuationMethod ? ` using ${methodLabel(valuationMethod)}` : ''} from the invoiced unit price.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer: approve-exception action (gated, ON_HOLD only) */}
        <div className="sticky bottom-0 bg-white border-t border-gray-100 px-5 py-3">
          {onHold ? (
            canApprove ? (
              <div className="flex flex-col tablet:flex-row tablet:items-end gap-2">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-600 mb-1">Reason for approval (required, audited)</label>
                  <input
                    className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Why release this invoice for payment despite the variance?"
                  />
                </div>
                <button type="button" onClick={approve} disabled={approving || !reason.trim()} className="btn sm primary disabled:opacity-60 whitespace-nowrap">
                  {approving ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />} Approve exception
                </button>
              </div>
            ) : (
              <p className="text-xs text-gray-500 flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-amber-500" />
                This invoice is on hold for a 3-way-match exception. Only an Admin, Accountant or Superadmin can approve it.
              </p>
            )
          ) : (
            <div className="flex justify-end">
              <button type="button" onClick={onClose} className="btn sm">Close</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Fallback when there's no match breakdown: render the invoice's own line items
// (description / qty / rate / GST / taxable) so the detail drawer is never blank.
function InvoiceLinesFallback({ invoice }: { invoice: PurchaseInvoice }) {
  const lines = invoice.lines ?? [];
  if (lines.length === 0) {
    return <p className="text-sm text-gray-500">No line items recorded on this invoice.</p>;
  }
  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-700 mb-2">Invoice lines</h4>
      <div className="overflow-x-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="text-left px-2 py-1.5">Item</th>
              <th className="text-left px-2 py-1.5">HSN</th>
              <th className="text-right px-2 py-1.5">Qty</th>
              <th className="text-right px-2 py-1.5">Rate</th>
              <th className="text-right px-2 py-1.5">GST%</th>
              <th className="text-right px-2 py-1.5">Taxable</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.map((l, i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-2 py-1.5 text-gray-900">{l.product_name || l.sku || `Line ${i + 1}`}</td>
                <td className="px-2 py-1.5 text-gray-500">{l.hsn_code || '-'}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.quantity)}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{l.unit_price != null ? inr(l.unit_price) : '-'}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.gst_rate)}%</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{l.taxable_amount != null ? inr(l.taxable_amount) : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
