// ============================================================================
// IMS 2.0 - Owner Cash-Flow + Accounts-Payable (SUPERADMIN / ADMIN / ACCOUNTANT)
// ============================================================================
// Three views in one screen:
//   Overview  - receivables vs payables, net position, this-month cash, alerts
//   Forecast  - weekly 30/60/90-day projection with a cash-crunch low point
//   AP aging  - payables by vendor; click a vendor to see its ledger + record
//               a bill / payment / debit-note.

import { useCallback, useEffect, useState } from 'react';
import {
  Banknote, TrendingDown, AlertTriangle, Loader2, X, Plus, RefreshCw,
} from 'lucide-react';
import {
  cashFlowApi, vendorApApi,
  type OwnerDashboard, type CashFlowForecast, type ApAgingByVendor, type VendorLedger,
} from '../../services/api/vendorAp';
import { useToast } from '../../context/ToastContext';

const inr = (n?: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;
const AP_BUCKETS = ['current', '1_30', '31_60', '61_90', '90_plus'];
const AP_LABELS: Record<string, string> = { current: 'Current', '1_30': '1-30d', '31_60': '31-60d', '61_90': '61-90d', '90_plus': '90+ d' };

function errMsg(e: unknown, fb: string) {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: string } } }).response;
    if (r?.data?.detail) return r.data.detail;
  }
  return e instanceof Error ? e.message : fb;
}

type Tab = 'overview' | 'forecast' | 'aging';

export default function CashFlowPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('overview');
  const [dash, setDash] = useState<OwnerDashboard | null>(null);
  const [forecast, setForecast] = useState<CashFlowForecast | null>(null);
  const [aging, setAging] = useState<ApAgingByVendor | null>(null);
  const [loading, setLoading] = useState(true);
  const [openingCash, setOpeningCash] = useState(0);
  const [activeVendor, setActiveVendor] = useState<{ id: string; name: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, f, a] = await Promise.all([
        cashFlowApi.ownerDashboard(),
        cashFlowApi.forecast({ days: 90, opening_cash: openingCash }),
        vendorApApi.apAging(),
      ]);
      setDash(d); setForecast(f); setAging(a);
    } catch (e) {
      toast.error(errMsg(e, 'Failed to load cash-flow'));
    } finally { setLoading(false); }
  }, [toast, openingCash]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Banknote className="w-5 h-5" /> Cash Flow &amp; Payables
        </h1>
        <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-5">
        {(['overview', 'forecast', 'aging'] as Tab[]).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'overview' ? 'Overview' : t === 'forecast' ? 'Forecast' : 'AP Aging'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div>
      ) : (
        <>
          {tab === 'overview' && dash && <Overview dash={dash} />}
          {tab === 'forecast' && forecast && (
            <Forecast forecast={forecast} openingCash={openingCash} setOpeningCash={setOpeningCash} onApply={load} />
          )}
          {tab === 'aging' && aging && <Aging aging={aging} onVendor={(id, name) => setActiveVendor({ id, name })} />}
        </>
      )}

      {activeVendor && (
        <VendorLedgerDrawer vendorId={activeVendor.id} vendorName={activeVendor.name} onClose={() => setActiveVendor(null)} onChanged={load} />
      )}
    </div>
  );
}

function Card({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn' }) {
  const color = tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : tone === 'warn' ? 'text-amber-700' : 'text-gray-900';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function Overview({ dash }: { dash: OwnerDashboard }) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="Receivables (AR)" value={inr(dash.receivables.total)} sub={`${inr(dash.receivables.overdue)} overdue 30+d`} tone="good" />
        <Card label="Payables (AP)" value={inr(dash.payables.total)} sub={`${inr(dash.payables.overdue)} overdue`} tone="bad" />
        <Card label="Net position" value={inr(dash.net_position)} sub="AR minus AP" tone={dash.net_position >= 0 ? 'good' : 'bad'} />
        <Card label="Due in 7 days (AP)" value={inr(dash.payables.due_7d)} sub={`${inr(dash.payables.due_30d)} in 30d`} tone="warn" />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="This month revenue" value={inr(dash.this_month.revenue)} tone="good" />
        <Card label="This month expenses" value={inr(dash.this_month.expenses)} />
        <Card label="Paid to vendors" value={inr(dash.this_month.vendor_payments)} />
        <Card label="Net cash flow (MTD)" value={inr(dash.this_month.net_cash_flow)} tone={dash.this_month.net_cash_flow >= 0 ? 'good' : 'bad'} />
      </div>

      {dash.alerts.length > 0 && (
        <div className="space-y-2">
          {dash.alerts.map((a, i) => (
            <div key={i} className={`flex items-center gap-2 text-sm rounded-lg px-3 py-2 border ${a.level === 'warning' ? 'bg-amber-50 border-amber-200 text-amber-800' : 'bg-blue-50 border-blue-200 text-blue-800'}`}>
              <AlertTriangle className="w-4 h-4 shrink-0" /> {a.message}
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <BucketBars title="Receivables aging" buckets={dash.receivables.buckets} order={['0_30', '31_60', '61_90', '90_plus']} labels={{ '0_30': '0-30d', '31_60': '31-60d', '61_90': '61-90d', '90_plus': '90+ d' }} />
        <BucketBars title="Payables aging" buckets={dash.payables.buckets} order={AP_BUCKETS} labels={AP_LABELS} />
      </div>
    </div>
  );
}

function BucketBars({ title, buckets, order, labels }: { title: string; buckets: Record<string, number>; order: string[]; labels: Record<string, string> }) {
  const max = Math.max(1, ...order.map((k) => buckets[k] || 0));
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-sm font-medium text-gray-700 mb-3">{title}</p>
      <div className="space-y-2">
        {order.map((k) => (
          <div key={k} className="flex items-center gap-2">
            <span className="text-xs text-gray-500 w-16 shrink-0">{labels[k]}</span>
            <div className="flex-1 bg-gray-100 rounded h-4 overflow-hidden">
              <div className={`h-4 ${k === '90_plus' ? 'bg-red-400' : k === 'current' || k === '0_30' ? 'bg-green-400' : 'bg-amber-400'}`} style={{ width: `${((buckets[k] || 0) / max) * 100}%` }} />
            </div>
            <span className="text-xs text-gray-700 w-20 text-right shrink-0">{inr(buckets[k])}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Forecast({ forecast, openingCash, setOpeningCash, onApply }: { forecast: CashFlowForecast; openingCash: number; setOpeningCash: (n: number) => void; onApply: () => void }) {
  const crunch = forecast.lowest.balance < 0;
  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Opening cash (bank balance today)</label>
          <input type="number" value={openingCash} onChange={(e) => setOpeningCash(parseFloat(e.target.value) || 0)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm w-48" />
        </div>
        <button type="button" onClick={onApply} className="text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg px-4 py-1.5">Apply</button>
      </div>

      {crunch && (
        <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2 border bg-red-50 border-red-200 text-red-800">
          <TrendingDown className="w-4 h-4" /> Projected cash crunch: balance dips to {inr(forecast.lowest.balance)} around {forecast.lowest.week_start}. Collect receivables or defer payables.
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <Card label="Expected inflow (90d)" value={inr(forecast.totals.inflow)} tone="good" />
        <Card label="Expected outflow (90d)" value={inr(forecast.totals.outflow)} tone="bad" />
        <Card label="Projected closing" value={inr(forecast.totals.closing_balance)} tone={forecast.totals.closing_balance >= 0 ? 'good' : 'bad'} />
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs">
            <tr>
              <th className="text-left px-3 py-2">Week of</th>
              <th className="text-right px-3 py-2">Inflow</th>
              <th className="text-right px-3 py-2">Outflow</th>
              <th className="text-right px-3 py-2">Net</th>
              <th className="text-right px-3 py-2">Balance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {forecast.weeks.map((w) => (
              <tr key={w.index} className={w.closing_balance < 0 ? 'bg-red-50' : ''}>
                <td className="px-3 py-1.5 text-gray-700">{w.start}</td>
                <td className="px-3 py-1.5 text-right text-green-700">{inr(w.inflow)}</td>
                <td className="px-3 py-1.5 text-right text-red-700">{inr(w.outflow)}</td>
                <td className={`px-3 py-1.5 text-right ${w.net >= 0 ? 'text-gray-700' : 'text-red-700'}`}>{inr(w.net)}</td>
                <td className={`px-3 py-1.5 text-right font-medium ${w.closing_balance < 0 ? 'text-red-700' : 'text-gray-900'}`}>{inr(w.closing_balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-400">
        Inflows = unpaid orders projected to collection; outflows = vendor bills on due date + recurring estimate
        ({inr(forecast.assumptions?.recurring_monthly_total)}/mo). Estimates, not commitments.
      </p>
    </div>
  );
}

function Aging({ aging, onVendor }: { aging: ApAgingByVendor; onVendor: (id: string, name: string) => void }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-500 text-xs">
          <tr>
            <th className="text-left px-3 py-2">Vendor</th>
            {AP_BUCKETS.map((b) => <th key={b} className="text-right px-3 py-2">{AP_LABELS[b]}</th>)}
            <th className="text-right px-3 py-2">Outstanding</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {aging.vendors.length === 0 ? (
            <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">No outstanding payables.</td></tr>
          ) : aging.vendors.map((v) => (
            <tr key={v.vendor_id} className="hover:bg-gray-50 cursor-pointer" onClick={() => onVendor(v.vendor_id, v.vendor_name || v.vendor_id)}>
              <td className="px-3 py-2 font-medium text-blue-700">{v.vendor_name || v.vendor_id}</td>
              {AP_BUCKETS.map((b) => <td key={b} className="px-3 py-2 text-right text-gray-600">{inr(v.buckets[b])}</td>)}
              <td className="px-3 py-2 text-right font-semibold text-gray-900">{inr(v.net_payable)}</td>
            </tr>
          ))}
        </tbody>
        {aging.vendors.length > 0 && (
          <tfoot className="bg-gray-50 font-medium">
            <tr>
              <td className="px-3 py-2">Total</td>
              {AP_BUCKETS.map((b) => <td key={b} className="px-3 py-2 text-right">{inr(aging.totals.buckets[b])}</td>)}
              <td className="px-3 py-2 text-right">{inr(aging.totals.net_payable)}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Vendor ledger drawer + record bill / payment / debit-note
// ---------------------------------------------------------------------------
function VendorLedgerDrawer({ vendorId, vendorName, onClose, onChanged }: { vendorId: string; vendorName: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [ledger, setLedger] = useState<VendorLedger | null>(null);
  const [loading, setLoading] = useState(true);
  const [recording, setRecording] = useState<'bill' | 'payment' | 'debit' | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setLedger(await vendorApApi.ledger(vendorId)); }
    catch (e) { toast.error(errMsg(e, 'Failed to load ledger')); }
    finally { setLoading(false); }
  }, [vendorId, toast]);
  useEffect(() => { load(); }, [load]);

  const refresh = () => { load(); onChanged(); };

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-white w-full max-w-xl h-full overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white">
          <h3 className="font-semibold text-gray-900">{vendorName}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5">
          {loading ? (
            <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div>
          ) : ledger ? (
            <>
              <div className="grid grid-cols-3 gap-2 mb-4">
                <Card label="Payable balance" value={inr(ledger.ledger.closing_balance)} tone="bad" />
                <Card label="Billed" value={inr(ledger.ledger.total_billed)} />
                <Card label="Paid" value={inr(ledger.ledger.total_paid)} sub={`TDS ${inr(ledger.ledger.total_tds)}`} />
              </div>

              <div className="flex gap-2 mb-4">
                <button type="button" onClick={() => setRecording('bill')} className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Bill</button>
                <button type="button" onClick={() => setRecording('payment')} className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Payment</button>
                <button type="button" onClick={() => setRecording('debit')} className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Debit note</button>
              </div>

              {recording && (
                <RecordForm kind={recording} vendorId={vendorId} onClose={() => setRecording(null)} onSaved={() => { setRecording(null); refresh(); }} />
              )}

              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Ledger</p>
              <table className="w-full text-xs">
                <thead className="text-gray-400">
                  <tr><th className="text-left py-1">Date</th><th className="text-left py-1">Type</th><th className="text-right py-1">Debit</th><th className="text-right py-1">Credit</th><th className="text-right py-1">Balance</th></tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {ledger.ledger.entries.length === 0 ? (
                    <tr><td colSpan={5} className="py-4 text-center text-gray-400">No transactions yet.</td></tr>
                  ) : ledger.ledger.entries.map((e, i) => (
                    <tr key={i}>
                      <td className="py-1 text-gray-600">{(e.date || '').slice(0, 10)}</td>
                      <td className="py-1 text-gray-700">{e.type}<span className="text-gray-400"> {e.ref || ''}</span></td>
                      <td className="py-1 text-right text-red-700">{e.debit ? inr(e.debit) : ''}</td>
                      <td className="py-1 text-right text-green-700">{e.credit ? inr(e.credit) : ''}</td>
                      <td className="py-1 text-right font-medium">{inr(e.balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function RecordForm({ kind, vendorId, onClose, onSaved }: { kind: 'bill' | 'payment' | 'debit'; vendorId: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [f, setF] = useState<Record<string, string | number>>({});
  const today = new Date().toISOString().slice(0, 10);
  const set = (k: string, v: string | number) => setF((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      if (kind === 'bill') {
        const taxable = Number(f.taxable_amount) || 0;
        const tax = Number(f.tax_amount) || 0;
        await vendorApApi.createBill(vendorId, {
          bill_number: String(f.bill_number || ''), bill_date: String(f.bill_date || today),
          taxable_amount: taxable, tax_amount: tax, total_amount: taxable + tax, notes: String(f.notes || ''),
        });
      } else if (kind === 'payment') {
        await vendorApApi.createPayment(vendorId, {
          amount: Number(f.amount) || 0, payment_date: String(f.payment_date || today),
          mode: String(f.mode || 'BANK'), tds_section: String(f.tds_section || 'NONE'),
          tds_amount: f.tds_amount !== undefined ? Number(f.tds_amount) : undefined,
          reference: String(f.reference || ''),
        });
      } else {
        await vendorApApi.createDebitNote(vendorId, {
          amount: Number(f.amount) || 0, date: String(f.date || today), reason: String(f.reason || ''),
        });
      }
      toast.success('Recorded');
      onSaved();
    } catch (e) { toast.error(errMsg(e, 'Failed to record')); }
    finally { setSaving(false); }
  };

  const cls = 'border border-gray-300 rounded px-2 py-1 text-sm w-full';
  return (
    <div className="border border-gray-200 rounded-lg p-3 mb-4 bg-gray-50/60">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium capitalize">{kind === 'debit' ? 'Debit note' : kind}</h4>
        <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-4 h-4" /></button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {kind === 'bill' && <>
          <input className={cls} placeholder="Bill / invoice no" onChange={(e) => set('bill_number', e.target.value)} />
          <input className={cls} type="date" defaultValue={today} onChange={(e) => set('bill_date', e.target.value)} />
          <input className={cls} type="number" placeholder="Taxable amount" onChange={(e) => set('taxable_amount', e.target.value)} />
          <input className={cls} type="number" placeholder="Tax (GST)" onChange={(e) => set('tax_amount', e.target.value)} />
          <div className="col-span-2 text-xs text-gray-500">Total: {inr((Number(f.taxable_amount) || 0) + (Number(f.tax_amount) || 0))}. Due date set from vendor credit terms.</div>
        </>}
        {kind === 'payment' && <>
          <input className={cls} type="number" placeholder="Amount paid" onChange={(e) => set('amount', e.target.value)} />
          <input className={cls} type="date" defaultValue={today} onChange={(e) => set('payment_date', e.target.value)} />
          <select className={cls} defaultValue="BANK" onChange={(e) => set('mode', e.target.value)}>
            {['BANK', 'CASH', 'UPI', 'CHEQUE', 'NEFT'].map((m) => <option key={m}>{m}</option>)}
          </select>
          <select className={cls} defaultValue="NONE" onChange={(e) => set('tds_section', e.target.value)}>
            {['NONE', '194C_IND', '194C_OTHER', '194J', '194Q', '194H'].map((m) => <option key={m}>{m}</option>)}
          </select>
          <input className={cls} type="number" placeholder="TDS amount (optional)" onChange={(e) => set('tds_amount', e.target.value)} />
          <input className={cls} placeholder="Reference / UTR" onChange={(e) => set('reference', e.target.value)} />
        </>}
        {kind === 'debit' && <>
          <input className={cls} type="number" placeholder="Amount" onChange={(e) => set('amount', e.target.value)} />
          <input className={cls} type="date" defaultValue={today} onChange={(e) => set('date', e.target.value)} />
          <input className={`${cls} col-span-2`} placeholder="Reason (e.g. rejected goods)" onChange={(e) => set('reason', e.target.value)} />
        </>}
      </div>
      <div className="flex justify-end mt-2">
        <button type="button" onClick={save} disabled={saving} className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg px-4 py-1.5 disabled:opacity-60">
          {saving && <Loader2 className="w-4 h-4 animate-spin" />} Save
        </button>
      </div>
    </div>
  );
}
