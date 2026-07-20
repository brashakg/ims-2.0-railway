// ============================================================================
// IMS 2.0 - GST reconciliation cross-check (accountant month-end sign-off)
// ============================================================================
// For a chosen month + legal entity, lays IMS's GSTR-1 / GSTR-3B numbers SIDE
// BY SIDE against the books (orders / payments / Tally sales-JV / purchase-side
// ITC), with a per-rate breakup, CDNR + deemed-supply (inter-GSTIN transfer)
// detail, and mismatch flags with drill-down. The accountant confirms it all
// agrees, then marks the month CHECKED with notes (audit-logged). It is a
// review aid only -- it changes no figure and does NOT lock the period.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  Loader2,
  ClipboardCheck,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import {
  gstCrossCheckApi,
  type GstCrossCheck,
  type CrossCheckRow,
} from '../../services/api/gstCrossCheck';
import { entitiesApi, type Entity } from '../../services/api/entities';
import { useToast } from '../../context/ToastContext';

const inr = (n?: number) =>
  `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

// Variance is shown to 2dp: at a ₹1 tolerance, a ₹0.99 MATCH and a ₹1.40
// MISMATCH must not both render as "₹1".
const inr2 = (n?: number) =>
  `₹${(Number(n) || 0).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

// Union of source labels across all comparison rows, in first-seen order, so
// the side-by-side matrix has a stable column per source.
function sourceColumns(rows: CrossCheckRow[]): string[] {
  const seen: string[] = [];
  for (const r of rows) {
    for (const k of Object.keys(r.sources)) {
      if (!seen.includes(k)) seen.push(k);
    }
  }
  return seen;
}

function StatusBadge({ status }: { status: CrossCheckRow['status'] }) {
  if (status === 'MATCH')
    return (
      <span className="inline-flex items-center gap-1 text-green-700 text-xs font-medium">
        <CheckCircle2 className="w-3.5 h-3.5" /> Match
      </span>
    );
  if (status === 'MISMATCH')
    return (
      <span className="inline-flex items-center gap-1 text-red-700 text-xs font-medium">
        <AlertTriangle className="w-3.5 h-3.5" /> Mismatch
      </span>
    );
  return <span className="text-gray-400 text-xs">--</span>;
}

export default function GstCrossCheckPage() {
  const toast = useToast();
  const [monthStr, setMonthStr] = useState<string>(currentMonth());
  const [entities, setEntities] = useState<Entity[]>([]);
  const [entityId, setEntityId] = useState<string>('');
  const [data, setData] = useState<GstCrossCheck | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState('');
  const [signingOff, setSigningOff] = useState(false);
  const [showCdnr, setShowCdnr] = useState(false);
  const [showDeemed, setShowDeemed] = useState(false);

  useEffect(() => {
    entitiesApi
      .list()
      .then((r) => setEntities(r.entities || []))
      .catch(() => setEntities([]));
  }, []);

  const [year, month] = useMemo(() => {
    const [y, m] = monthStr.split('-').map((x) => parseInt(x, 10));
    return [y, m];
  }, [monthStr]);

  const load = useCallback(async () => {
    if (!year || !month) return;
    setLoading(true);
    try {
      const res = await gstCrossCheckApi.get(month, year, entityId || undefined);
      setData(res);
      setNote(res.signoff?.note || '');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load cross-check');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [year, month, entityId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const doSignoff = async () => {
    if (!data) return;
    setSigningOff(true);
    try {
      const res = await gstCrossCheckApi.signoff({
        month,
        year,
        entity_id: entityId || undefined,
        note: note || undefined,
        mismatch_count: data.summary.mismatch_count,
        gst_payable: data.summary.gst_payable,
      });
      toast.success('Month marked CHECKED');
      setData({ ...data, signoff: res.signoff });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Sign-off failed');
    } finally {
      setSigningOff(false);
    }
  };

  const cols = useMemo(
    () => (data ? sourceColumns(data.comparisons) : []),
    [data]
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-1">
        <ClipboardCheck className="w-5 h-5" /> GST Cross-Check
      </h1>
      <p className="text-sm text-gray-500 mb-5">
        Reconcile IMS's GSTR-1 / GSTR-3B against the books (orders, payments,
        Tally, purchase ITC) for a month + entity, then sign the month off. A
        review aid -- it does not change figures or lock the period.
      </p>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3 mb-5">
        <label className="text-xs text-gray-600">
          <span className="block mb-1">Tax period</span>
          <input
            type="month"
            value={monthStr}
            onChange={(e) => setMonthStr(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          />
        </label>
        <label className="text-xs text-gray-600">
          <span className="block mb-1">Legal entity</span>
          <select
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white min-w-[16rem]"
          >
            <option value="">All entities</option>
            {entities.map((e) => (
              <option key={e.entity_id} value={e.entity_id}>
                {e.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5 disabled:opacity-60"
        >
          {loading && <Loader2 className="w-4 h-4 animate-spin" />} Refresh
        </button>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-2 text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading...
        </div>
      ) : data ? (
        <>
          {/* Status banner */}
          <div
            className={`mb-5 flex items-center justify-between gap-3 px-4 py-3 rounded-lg border ${
              data.summary.all_matched
                ? 'bg-green-50 border-green-200'
                : 'bg-amber-50 border-amber-200'
            }`}
            role="status"
            aria-live="polite"
          >
            <div className="flex items-center gap-2">
              {data.summary.all_matched ? (
                <CheckCircle2 className="w-5 h-5 text-green-600" />
              ) : (
                <AlertTriangle className="w-5 h-5 text-amber-600" />
              )}
              <span
                className={`text-sm font-medium ${
                  data.summary.all_matched ? 'text-green-800' : 'text-amber-800'
                }`}
              >
                {data.summary.all_matched
                  ? 'All sources reconcile within tolerance.'
                  : `${data.summary.mismatch_count} mismatch${
                      data.summary.mismatch_count === 1 ? '' : 'es'
                    }: ${data.summary.mismatch_metrics.join(', ')}`}
              </span>
            </div>
            <div className="text-xs text-gray-500">
              {data.entity_name} · {data.store_count} store
              {data.store_count === 1 ? '' : 's'} · net payable{' '}
              <span className="font-semibold text-gray-900">
                {inr(data.summary.gst_payable)}
              </span>
            </div>
          </div>

          {/* Partial-data warning: some stores failed to compute, so the GSTR
              columns are understated. Sign-off is blocked until it clears. */}
          {data.partial && (
            <div className="mb-5 flex items-start gap-2 px-4 py-3 rounded-lg border border-red-200 bg-red-50 text-sm text-red-800">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <div>
                Partial data: {data.stores_computed} of {data.store_count} store
                {data.store_count === 1 ? '' : 's'} computed
                {data.failed_store_ids.length > 0
                  ? ` (failed: ${data.failed_store_ids.join(', ')})`
                  : ''}
                . The GSTR figures are understated — do not sign off until this
                is resolved.
              </div>
            </div>
          )}

          {/* Existing sign-off note */}
          {data.signoff?.checked && (
            <div className="mb-5 flex items-start gap-2 px-4 py-2.5 rounded-lg border border-green-200 bg-green-50 text-sm text-green-800">
              <ShieldCheck className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <div>
                Checked by{' '}
                <span className="font-medium">
                  {data.signoff.checked_by_name || 'accountant'}
                </span>
                {data.signoff.checked_at
                  ? ` on ${String(data.signoff.checked_at).slice(0, 10)}`
                  : ''}
                {data.signoff.note ? ` — "${data.signoff.note}"` : ''}
              </div>
            </div>
          )}

          {/* Side-by-side comparison matrix */}
          <SectionTitle>Side-by-side totals</SectionTitle>
          <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto mb-6">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">Metric</th>
                  {cols.map((c) => (
                    <th key={c} className="text-right px-4 py-2 whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                  <th className="text-right px-4 py-2">Variance</th>
                  <th className="text-right px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.comparisons.map((row) => (
                  <tr
                    key={row.metric}
                    className={row.status === 'MISMATCH' ? 'bg-red-50/50' : ''}
                  >
                    <td className="px-4 py-2 text-gray-700">
                      {row.metric}
                      {row.note && (
                        <span
                          className="ml-1 text-gray-300 cursor-help"
                          title={row.note}
                        >
                          &#9432;
                        </span>
                      )}
                    </td>
                    {cols.map((c) => (
                      <td key={c} className="px-4 py-2 text-right text-gray-700">
                        {c in row.sources ? inr(row.sources[c]) : <span className="text-gray-300">--</span>}
                      </td>
                    ))}
                    <td
                      className={`px-4 py-2 text-right ${
                        row.status === 'MISMATCH'
                          ? 'text-red-700 font-medium'
                          : 'text-gray-500'
                      }`}
                    >
                      {Object.keys(row.sources).length >= 2
                        ? inr2(row.variance)
                        : '--'}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <StatusBadge status={row.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Per-rate breakup */}
          <SectionTitle>Per-rate breakup (GSTR-1, net of credit notes)</SectionTitle>
          <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto mb-6">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">GST rate</th>
                  <th className="text-right px-4 py-2">Taxable</th>
                  <th className="text-right px-4 py-2">CGST</th>
                  <th className="text-right px-4 py-2">SGST</th>
                  <th className="text-right px-4 py-2">IGST</th>
                  <th className="text-right px-4 py-2">Total tax</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rate_breakup.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-6 text-center text-gray-400">
                      No taxable outward supplies for this period.
                    </td>
                  </tr>
                ) : (
                  data.rate_breakup.map((r) => (
                    <tr key={r.gstRate}>
                      <td className="px-4 py-2 text-gray-700">{r.gstRate}%</td>
                      <td className="px-4 py-2 text-right">{inr(r.taxableValue)}</td>
                      <td className="px-4 py-2 text-right text-gray-500">{inr(r.cgst)}</td>
                      <td className="px-4 py-2 text-right text-gray-500">{inr(r.sgst)}</td>
                      <td className="px-4 py-2 text-right text-gray-500">{inr(r.igst)}</td>
                      <td className="px-4 py-2 text-right font-medium">{inr(r.tax)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* CDNR + deemed-supply detail (drill-down) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <DrillCard
              title="Credit / debit notes (CDNR)"
              count={data.cdnr.count}
              taxable={data.cdnr.taxableValue}
              tax={data.cdnr.tax}
              open={showCdnr}
              onToggle={() => setShowCdnr((s) => !s)}
              rows={data.cdnr.rows}
              cols={['refReference', 'customerName', 'taxableValue', 'taxValue', 'gstRate']}
            />
            <DrillCard
              title="Deemed-supply transfers (inter-GSTIN)"
              count={data.deemed_supply.count}
              taxable={data.deemed_supply.taxableValue}
              tax={data.deemed_supply.tax}
              open={showDeemed}
              onToggle={() => setShowDeemed((s) => !s)}
              rows={data.deemed_supply.rows}
              cols={['invoiceNumber', 'customerGSTIN', 'taxableValue', 'totalTax', 'gstRate']}
            />
          </div>

          {/* Validation warnings from the GSTR-1 build */}
          {data.validation && !data.validation.ok && (
            <div className="mb-6 px-4 py-3 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-800">
              <p className="font-medium mb-1 flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4" /> {data.validation.issueCount}{' '}
                GSTR-1 validation warning
                {data.validation.issueCount === 1 ? '' : 's'}
              </p>
              <ul className="list-disc list-inside text-xs space-y-0.5">
                {data.validation.issues.slice(0, 8).map((iss, i) => (
                  <li key={i}>
                    {String(iss.issue ?? '')}
                    {iss.invoice ? ` (${String(iss.invoice)})` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Sign-off */}
          <SectionTitle>Accountant sign-off</SectionTitle>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-xs text-gray-500 mb-2">
              Marks this month + entity as reviewed. Audit-logged. Does not lock
              the accounting period.
            </p>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="Notes (e.g. mismatches explained, adjustments booked in Tally)..."
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-3"
            />
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={doSignoff}
                disabled={signingOff || data.partial}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5 disabled:opacity-60"
              >
                {signingOff ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <ShieldCheck className="w-4 h-4" />
                )}
                {data.signoff?.checked ? 'Re-check month' : 'Mark month CHECKED'}
              </button>
              {data.partial && (
                <span className="text-xs text-red-700">
                  Sign-off blocked: some stores failed to compute.
                </span>
              )}
              {!data.partial && !data.summary.all_matched && (
                <span className="text-xs text-amber-700">
                  {data.summary.mismatch_count} unreconciled mismatch
                  {data.summary.mismatch_count === 1 ? '' : 'es'} -- add a note
                  before signing off.
                </span>
              )}
            </div>
          </div>
        </>
      ) : (
        <p className="text-sm text-gray-400">No data.</p>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-sm font-semibold text-gray-700 mb-2">{children}</h2>;
}

function DrillCard({
  title,
  count,
  taxable,
  tax,
  open,
  onToggle,
  rows,
  cols,
}: {
  title: string;
  count: number;
  taxable: number;
  tax: number;
  open: boolean;
  onToggle: () => void;
  rows: Array<Record<string, unknown>>;
  cols: string[];
}) {
  const isMoney = (k: string) => /tax|taxable|value|amount/i.test(k) && !/rate/i.test(k);
  return (
    <div className="bg-white border border-gray-200 rounded-lg">
      <button
        type="button"
        onClick={onToggle}
        disabled={count === 0}
        className="w-full flex items-center justify-between gap-2 px-4 py-3 text-left disabled:cursor-default"
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5 text-sm font-medium text-gray-700">
          {count > 0 &&
            (open ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            ))}
          {title}
        </span>
        <span className="text-xs text-gray-500">
          {count} · {inr(taxable)} taxable · {inr(tax)} tax
        </span>
      </button>
      {open && count > 0 && (
        <div className="overflow-x-auto border-t border-gray-100">
          <table className="w-full text-xs">
            <thead className="text-gray-400">
              <tr>
                {cols.map((c) => (
                  <th
                    key={c}
                    className={`px-3 py-1.5 ${isMoney(c) ? 'text-right' : 'text-left'}`}
                  >
                    {c.replace(/([A-Z])/g, ' $1').replace(/_/g, ' ').trim()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.slice(0, 100).map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td
                      key={c}
                      className={`px-3 py-1.5 text-gray-700 ${
                        isMoney(c) ? 'text-right' : 'text-left'
                      }`}
                    >
                      {isMoney(c) ? inr(r[c] as number) : String(r[c] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
