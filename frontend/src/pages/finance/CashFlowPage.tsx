// ============================================================================
// IMS 2.0 - Owner Cash-Flow + Accounts-Payable (SUPERADMIN / ADMIN / ACCOUNTANT)
// ============================================================================
// Three views in one screen:
//   Overview  - receivables vs payables, net position, this-month cash, alerts
//   Forecast  - weekly 30/60/90-day projection with a cash-crunch low point
//   AP aging  - payables by vendor; click a vendor to see its ledger + record
//               a bill / payment / debit-note.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Banknote, TrendingDown, AlertTriangle, Loader2, X, Plus, RefreshCw,
} from 'lucide-react';
import {
  cashFlowApi, vendorApApi,
  type OwnerDashboard, type CashFlowForecast, type ApAgingByVendor, type VendorLedger,
  type VendorReceiptRow, type VendorBill, type VendorPayment,
} from '../../services/api/vendorAp';
import { useToast } from '../../context/ToastContext';
import { PurchaseShopName, usePurchaseShop } from '../purchase/purchaseShop';
import { PurchaseShopField, usePurchaseShopNamer } from '../purchase/invoices/InvoiceFormDrawer';
import { istDayString } from '../../utils/datetime';

const inr = (n?: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;
const AP_BUCKETS = ['current', '1_30', '31_60', '61_90', '90_plus'];
const AP_LABELS: Record<string, string> = { current: 'Current', '1_30': '1-30d', '31_60': '31-60d', '61_90': '61-90d', '90_plus': '90+ d' };

function errMsg(e: unknown, fb: string) {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: string | { message?: string } } } }).response;
    const d = r?.data?.detail;
    // Structured refusals ({code, message}) carry their text in .message —
    // rendering the raw object printed "[object Object]" in the toast.
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && d.message) return d.message;
  }
  return e instanceof Error ? e.message : fb;
}

type Tab = 'overview' | 'forecast' | 'aging';

/** Every figure here is one shop's or (admins) every shop's. A non-admin
 *  login with no shop never reaches this screen: its route is wrapped in
 *  NoShopGate (routes/financeRoutes, owner ruling 2026-10-12 R4). */
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
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Banknote className="w-5 h-5" /> Cash Flow &amp; Payables
          </h1>
          {dash && <PayablesShop storeId={dash.store_id} />}
        </div>
        <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-5">
        {(['overview', 'forecast', 'aging'] as Tab[]).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t ? 'border-bv text-bv' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
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
          {tab === 'aging' && aging && (
            <div className="space-y-2">
              <PayablesShop storeId={dash?.store_id} testId="aging-shop" />
              <Aging aging={aging} onVendor={(id, name) => setActiveVendor({ id, name })} />
            </div>
          )}
        </>
      )}

      {activeVendor && (
        <VendorLedgerDrawer vendorId={activeVendor.id} vendorName={activeVendor.name} onClose={() => setActiveVendor(null)} onChanged={load} />
      )}
    </div>
  );
}

/** Which shop every figure on this page covers (audit F63): the server scopes
 *  the owner dashboard, the forecast and AP aging alike -- every shop for an
 *  admin, the caller's own shop for anyone else -- and says which in the
 *  dashboard's store_id. Nothing when an older server leaves it out. */
function PayablesShop({ storeId, testId = 'payables-shop' }: { storeId: string | null | undefined; testId?: string }) {
  if (storeId === undefined) return null;
  return (
    <p className="text-xs text-gray-600 mt-0.5" data-testid={testId}>
      {storeId ? (
        <>
          Shop: <span className="font-medium text-gray-900"><PurchaseShopName storeId={storeId} /></span>
        </>
      ) : (
        'All shops'
      )}
    </p>
  );
}

function Card({ label, value, sub, tone, testId }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn'; testId?: string }) {
  const color = tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : tone === 'warn' ? 'text-amber-700' : 'text-gray-900';
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4" data-testid={testId}>
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

// THE ONE 'WE OWE' RULE (audit F56): what we owe is the sum of each
// supplier's balance above 0 -- the aging bars added up. Money paid ahead to
// suppliers (each supplier's balance below 0) is its own figure, never taken
// off what we owe: one supplier's advance does not pay another's bills.
// The older key names stand in for an older server.
const paidAhead = (p: { advances?: number; unallocated_credits?: number }) =>
  p.advances ?? p.unallocated_credits ?? 0;
const paidAheadWords = (n: number) => `${inr(n)} paid ahead to suppliers`;

function Overview({ dash }: { dash: OwnerDashboard }) {
  const ahead = paidAhead(dash.payables);
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="Receivables (AR)" value={inr(dash.receivables.total)} sub={`${inr(dash.receivables.overdue)} overdue 30+d`} tone="good" />
        {/* F56: the headline, the bars and 'overdue' are what we owe on
            bills; an advance is named apart, never subtracted. */}
        <Card
          label="Payables (AP)"
          value={inr(dash.payables.total)}
          sub={`${inr(dash.payables.overdue)} overdue${ahead > 0 ? ` · ${paidAheadWords(ahead)}, not taken off` : ''}`}
          tone="bad"
          testId="payables-card"
        />
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
        <BucketBars
          title="Payables aging"
          buckets={dash.payables.buckets}
          order={AP_BUCKETS}
          labels={AP_LABELS}
          note={ahead > 0 ? `We owe ${inr(dash.payables.total)}. Separately, ${paidAheadWords(ahead)}: each advance settles only that supplier's own bills, so it is not taken off what we owe.` : undefined}
          testId="payables-aging"
        />
      </div>
    </div>
  );
}

function BucketBars({ title, buckets, order, labels, note, testId }: { title: string; buckets: Record<string, number>; order: string[]; labels: Record<string, string>; note?: string; testId?: string }) {
  const max = Math.max(1, ...order.map((k) => buckets[k] || 0));
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4" data-testid={testId}>
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
      {note && <p className="text-xs text-gray-500 mt-3">{note}</p>}
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
        <button type="button" onClick={onApply} className="text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5">Apply</button>
      </div>

      {crunch && (
        <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2 border bg-red-50 border-red-200 text-red-800">
          <TrendingDown className="w-4 h-4" /> Projected cash crunch: balance dips to {inr(forecast.lowest.balance)} around {forecast.lowest.week_start}. Collect receivables or defer payables.
        </div>
      )}

      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
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
  const ahead = paidAhead(aging.totals);
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
          ) : aging.vendors.map((v) => {
            // A supplier we paid ahead shows its own advance; it owes nothing.
            const owed = v.owed ?? v.net_payable;
            const advance = paidAhead(v);
            return (
              <tr key={v.vendor_id} className="hover:bg-gray-50 cursor-pointer" onClick={() => onVendor(v.vendor_id, v.vendor_name || v.vendor_id)}>
                <td className="px-3 py-2 font-medium text-bv">{v.vendor_name || v.vendor_id}</td>
                {AP_BUCKETS.map((b) => <td key={b} className="px-3 py-2 text-right text-gray-600">{inr(v.buckets[b])}</td>)}
                <td className="px-3 py-2 text-right font-semibold text-gray-900" data-testid="aging-vendor-owed">
                  {owed <= 0 && advance > 0 ? <span className="font-medium text-blue-700">{inr(advance)} advance</span> : inr(owed)}
                </td>
              </tr>
            );
          })}
        </tbody>
        {aging.vendors.length > 0 && (
          <tfoot className="bg-gray-50 font-medium">
            <tr data-testid="aging-total">
              <td className="px-3 py-2">Total we owe</td>
              {AP_BUCKETS.map((b) => <td key={b} className="px-3 py-2 text-right">{inr(aging.totals.buckets[b])}</td>)}
              <td className="px-3 py-2 text-right">{inr(aging.totals.owed ?? aging.totals.net_payable)}</td>
            </tr>
            {ahead > 0 && (
              <tr className="text-gray-600 font-normal" data-testid="aging-paid-ahead">
                <td className="px-3 py-2" colSpan={AP_BUCKETS.length + 1}>
                  Paid ahead to suppliers (each advance settles only that supplier&rsquo;s own bills; not taken off the total)
                </td>
                <td className="px-3 py-2 text-right">{inr(ahead)}</td>
              </tr>
            )}
          </tfoot>
        )}
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Vendor ledger drawer + record bill / payment / debit-note
// ---------------------------------------------------------------------------
// The ledger's balance counts rows dated up to today. Rows dated later (a
// post-dated cheque, a bill keyed ahead) come back apart in `post_dated` --
// recorded, shown, not yet counted -- with no running balance.
type PostDatedEntry = Omit<VendorLedger['ledger']['entries'][number], 'balance'>;

function VendorLedgerDrawer({ vendorId, vendorName, onClose, onChanged }: { vendorId: string; vendorName: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [ledger, setLedger] = useState<VendorLedger | null>(null);
  const [loading, setLoading] = useState(true);
  const [recording, setRecording] = useState<RecordKind | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setLedger(await vendorApApi.ledger(vendorId)); }
    catch (e) { toast.error(errMsg(e, 'Failed to load ledger')); }
    finally { setLoading(false); }
  }, [vendorId, toast]);
  useEffect(() => { load(); }, [load]);

  const refresh = () => { load(); onChanged(); };
  const postDated = (ledger?.ledger as { post_dated?: PostDatedEntry[] } | undefined)?.post_dated ?? [];

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
              <div className="grid grid-cols-1 tablet:grid-cols-3 gap-2 mb-4">
                {/* This supplier's own signed balance: below 0 we paid it ahead
                    (read as its advance, as its AP Aging row does). */}
                {ledger.ledger.closing_balance < 0 ? (
                  <Card label="Payable balance" value={`${inr(-ledger.ledger.closing_balance)} advance`} sub="Paid ahead of this supplier's bills" testId="ledger-balance" />
                ) : (
                  <Card label="Payable balance" value={inr(ledger.ledger.closing_balance)} tone="bad" testId="ledger-balance" />
                )}
                <Card label="Billed" value={inr(ledger.ledger.total_billed)} />
                <Card label="Paid" value={inr(ledger.ledger.total_paid)} sub={`TDS ${inr(ledger.ledger.total_tds)}`} />
              </div>

              <div className="flex gap-2 mb-4">
                <button type="button" onClick={() => setRecording('bill')} className="inline-flex items-center gap-1 text-xs font-medium text-bv bg-bv-soft hover:bg-bv-50 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Bill</button>
                <button type="button" onClick={() => setRecording('payment')} className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Payment</button>
                <button type="button" onClick={() => setRecording('debit')} className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 rounded-lg px-2.5 py-1.5"><Plus className="w-3 h-3" /> Debit note</button>
                <button type="button" onClick={() => setRecording('apply')} className="inline-flex items-center gap-1 text-xs font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg px-2.5 py-1.5">Apply advance</button>
              </div>

              {/* key: each kind mounts its own fresh form. Kept mounted across
                  Payment -> Debit note, the amount and shop typed for one were
                  saved for the other while the new form's boxes showed empty. */}
              {recording && (
                <RecordForm key={recording} kind={recording} vendorId={vendorId} onClose={() => setRecording(null)} onSaved={() => { setRecording(null); refresh(); }} />
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

              {postDated.length > 0 && (
                <div className="mt-5" data-testid="ledger-post-dated">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Post-dated: not in the balance yet</p>
                  <p className="text-xs text-gray-500 mb-2">Recorded with a later date, such as a post-dated cheque. Each one counts on its own date.</p>
                  <table className="w-full text-xs">
                    <thead className="text-gray-400">
                      <tr><th className="text-left py-1">Date</th><th className="text-left py-1">Type</th><th className="text-right py-1">Debit</th><th className="text-right py-1">Credit</th></tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {postDated.map((e, i) => (
                        <tr key={i}>
                          <td className="py-1 text-gray-600">{(e.date || '').slice(0, 10)}</td>
                          <td className="py-1 text-gray-700">{e.type}<span className="text-gray-400"> {e.ref || ''}</span></td>
                          <td className="py-1 text-right text-red-700">{e.debit ? inr(e.debit) : ''}</td>
                          <td className="py-1 text-right text-green-700">{e.credit ? inr(e.credit) : ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

type RecordKind = 'bill' | 'payment' | 'debit' | 'apply';

// The payment form's "Apply this payment to" choice for money that names no
// bill (owner 2026-10-18, A12): an advance, applied to a bill later.
const ADVANCE = '__advance__';
const RECORD_TITLES: Record<RecordKind, string> = {
  bill: 'Bill', payment: 'Payment', debit: 'Debit note', apply: 'Apply an advance to a bill',
};
const gross = (p: VendorPayment) => Math.round(((p.amount || 0) + (p.tds_amount || 0)) * 100) / 100;

function RecordForm({ kind, vendorId, onClose, onSaved }: { kind: RecordKind; vendorId: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const shopName = usePurchaseShopNamer();
  const { canPick } = usePurchaseShop();
  const [saving, setSaving] = useState(false);
  const [f, setF] = useState<Record<string, string | number>>({});
  // Payments pick their bill (owner 2026-10-18): this supplier's open bills
  // (null = loading), the bill picked ('' = not chosen yet, ADVANCE = no bill),
  // and for 'apply' the unapplied advance picked plus the amount to apply.
  const [bills, setBills] = useState<VendorBill[] | null>(null);
  const [billsFailed, setBillsFailed] = useState(false);
  const [applyTo, setApplyTo] = useState('');
  const [advances, setAdvances] = useState<VendorPayment[] | null>(null);
  const [advanceId, setAdvanceId] = useState('');
  const [applyAmount, setApplyAmount] = useState('');
  const pickedBill = (bills ?? []).find((b) => b.bill_id === applyTo);
  const pickedAdvance = (advances ?? []).find((p) => p.payment_id === advanceId);
  const [creditType, setCreditType] = useState<'' | 'RETURN_CN' | 'SCHEME_CN' | 'DISCOUNT_CN' | 'QUALITY_CN'>('');
  // The IST business day, like every other bill door: toISOString() is the
  // UTC day, so a bill keyed at 01:00 IST on 1 October booked 30 September
  // and landed on September's GSTR-3B.
  const today = istDayString(new Date()) ?? '';
  const set = (k: string, v: string | number) => setF((p) => ({ ...p, [k]: v }));

  // Goods bills must link their goods receipt (owner ruling 15) — this form
  // used to book "20 pcs assorted frames" as prose with nothing to tally.
  // The choice is REQUIRED; GOODS then requires picking one of the vendor's
  // ACCEPTED receipts (a PO-backed GRN or a no-PO Delivery Challan).
  const [billKind, setBillKind] = useState<'' | 'GOODS' | 'SERVICES'>('');
  // null = not loaded yet / load failed (distinct from a real empty list).
  const [receipts, setReceipts] = useState<VendorReceiptRow[] | null>(null);
  const [receiptsFailed, setReceiptsFailed] = useState(false);
  const [receiptId, setReceiptId] = useState('');
  // The shop a payment / debit note is booked to (F63). '' = the supplier's
  // shop by its bills: the server places an admin's money that names no bill
  // there, never on the admin's own topbar shop, and its answer carries the
  // shop it booked -- the toast names it (bookedTo). Only an admin can pick
  // one (PurchaseShopField); anyone else's money is their own shop's, server-side.
  const [moneyShop, setMoneyShop] = useState('');
  // The shop a BILL is for (owner ruling 2026-10-12, R9). A goods bill is its
  // receipt's shop. A services bill (rent, freight) -- or a legacy receipt
  // with no shop on record -- has no shop of its own: an admin must pick one
  // (no default; the server refuses it without, and never books it to his
  // topbar shop). Anyone else's is their own shop's, shown read-only.
  const [billShop, setBillShop] = useState('');
  const pickedReceipt = (receipts ?? []).find((r) => r.grn_id === receiptId);
  const billNeedsShop = billKind === 'SERVICES' || (billKind === 'GOODS' && !!pickedReceipt && !pickedReceipt.store_id);
  const sendBillShop = canPick && billNeedsShop;

  useEffect(() => {
    if (kind !== 'bill' || billKind !== 'GOODS') return;
    let cancelled = false;
    vendorApApi.listReceipts(vendorId).then((rows) => {
      if (cancelled) return;
      setReceipts(rows ?? []);
      setReceiptsFailed(rows === null);
    });
    return () => { cancelled = true; };
  }, [kind, billKind, vendorId]);

  useEffect(() => {
    if (kind !== 'payment' && kind !== 'apply') return;
    let cancelled = false;
    (async () => {
      try {
        const res = await vendorApApi.listBills(vendorId);
        if (!cancelled) setBills((res?.bills ?? []).filter((b) => (b.outstanding ?? 0) > 0));
      } catch {
        if (!cancelled) { setBills([]); setBillsFailed(true); }
      }
      if (kind !== 'apply') return;
      try {
        const res = await vendorApApi.listPayments(vendorId);
        if (!cancelled) setAdvances((res?.payments ?? []).filter((p) => !String(p.bill_id ?? '').trim() && gross(p) > 0));
      } catch {
        if (!cancelled) setAdvances([]);
      }
    })();
    return () => { cancelled = true; };
  }, [kind, vendorId]);

  // Apply: offer the most that can go, never more than the advance or the bill.
  const pickForApply = (advId: string, billId: string) => {
    setAdvanceId(advId); setApplyTo(billId);
    const adv = (advances ?? []).find((p) => p.payment_id === advId);
    const bill = (bills ?? []).find((b) => b.bill_id === billId);
    if (adv && bill) setApplyAmount(String(Math.min(gross(adv), bill.outstanding ?? 0)));
  };

  const save = async () => {
    if (kind === 'payment' && !applyTo) {
      toast.error('Pick the bill this payment is for, or choose Advance (no bill yet)');
      return;
    }
    if (kind === 'apply' && (!pickedAdvance || !pickedBill)) {
      toast.error('Pick the advance and the bill to apply it to');
      return;
    }
    if (kind === 'debit' && (!creditType || creditType === 'RETURN_CN')) {
      toast.error(creditType === 'RETURN_CN'
        ? 'Record the supplier’s confirmed goods-return credit in Vendor Returns.'
        : 'Choose the type of credit adjustment');
      return;
    }
    if (kind === 'bill' && !billKind) {
      toast.error('Say what this bill is for: goods, or services/expenses');
      return;
    }
    if (kind === 'bill' && billKind === 'GOODS' && !receiptId) {
      toast.error('Pick the goods receipt this bill settles — no receipt, no goods bill');
      return;
    }
    if (kind === 'bill' && sendBillShop && !billShop) {
      toast.error('Pick the shop this bill is for');
      return;
    }
    setSaving(true);
    try {
      if (kind === 'bill') {
        const taxable = Number(f.taxable_amount) || 0;
        const tax = Number(f.tax_amount) || 0;
        const saved = await vendorApApi.createBill(vendorId, {
          bill_number: String(f.bill_number || ''), bill_date: String(f.bill_date || today),
          taxable_amount: taxable, tax_amount: tax, total_amount: taxable + tax, notes: String(f.notes || ''),
          bill_kind: billKind as 'GOODS' | 'SERVICES',
          grn_id: billKind === 'GOODS' ? receiptId : undefined,
          store_id: sendBillShop ? billShop : undefined,
        });
        toast.success(bookedTo('Bill', saved, shopName));
      } else if (kind === 'payment') {
        const saved = await vendorApApi.createPayment(vendorId, {
          amount: Number(f.amount) || 0, payment_date: String(f.payment_date || today),
          mode: String(f.mode || 'BANK'), tds_section: String(f.tds_section || 'NONE'),
          tds_amount: f.tds_amount !== undefined ? Number(f.tds_amount) : undefined,
          reference: String(f.reference || ''),
          // A bill's money is the bill's shop's: no shop is sent with it.
          bill_id: pickedBill ? pickedBill.bill_id : undefined,
          store_id: pickedBill ? undefined : moneyShop || undefined,
        });
        toast.success(bookedTo(pickedBill ? `Payment against bill ${pickedBill.bill_number}` : 'Payment', saved, shopName));
      } else if (kind === 'apply' && pickedAdvance && pickedBill) {
        const out = await vendorApApi.applyAdvance(vendorId, pickedAdvance.payment_id, {
          bill_id: pickedBill.bill_id, amount: Number(applyAmount) || 0,
        });
        toast.success(out.advance_left > 0
          ? `Applied to bill ${pickedBill.bill_number}; ${inr(out.advance_left)} of the advance is still unapplied`
          : `Advance applied to bill ${pickedBill.bill_number}`);
      } else {
        const saved = await vendorApApi.createDebitNote(vendorId, {
          amount: Number(f.amount) || 0, date: String(f.date || today), reason: String(f.reason || ''),
          store_id: moneyShop || undefined,
          cn_type: creditType as 'SCHEME_CN' | 'DISCOUNT_CN' | 'QUALITY_CN',
        });
        toast.success(bookedTo('Debit note', saved, shopName));
      }
      onSaved();
    } catch (e) { toast.error(errMsg(e, 'Failed to record')); }
    finally { setSaving(false); }
  };

  const cls = 'border border-gray-300 rounded px-2 py-1 text-sm w-full';
  return (
    <div className="border border-gray-200 rounded-lg p-3 mb-4 bg-gray-50/60">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium">{RECORD_TITLES[kind]}</h4>
        <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-4 h-4" /></button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {kind === 'bill' && <>
          <select
            className={`${cls} col-span-2`}
            value={billKind}
            onChange={(e) => { setBillKind(e.target.value as '' | 'GOODS' | 'SERVICES'); setReceiptId(''); }}
          >
            <option value="">This bill is for…</option>
            <option value="GOODS">Goods (frames, lenses, stock — needs the goods receipt)</option>
            <option value="SERVICES">Services / expenses (rent, freight, job-work)</option>
          </select>
          {billKind === 'GOODS' && (
            <ReceiptPicker
              receipts={receipts}
              failed={receiptsFailed}
              value={receiptId}
              onChange={setReceiptId}
              className={cls}
              shopName={shopName}
            />
          )}
          {billNeedsShop && (
            <PurchaseShopField
              required
              value={billShop}
              onChange={setBillShop}
              label="Shop this bill is for"
              emptyLabel="Pick the shop this bill is for…"
              className="col-span-2"
              testId="bill-shop"
            />
          )}
          <input className={cls} placeholder="Bill / invoice no" onChange={(e) => set('bill_number', e.target.value)} />
          <input className={cls} type="date" defaultValue={today} onChange={(e) => set('bill_date', e.target.value)} />
          <input className={cls} type="number" placeholder="Taxable amount" onChange={(e) => set('taxable_amount', e.target.value)} />
          <input className={cls} type="number" placeholder="Tax (GST)" onChange={(e) => set('tax_amount', e.target.value)} />
          <div className="col-span-2 text-xs text-gray-500">Total: {inr((Number(f.taxable_amount) || 0) + (Number(f.tax_amount) || 0))}. Due date set from vendor credit terms.</div>
        </>}
        {kind === 'payment' && <>
          <BillPicker
            bills={bills} failed={billsFailed} value={applyTo} onChange={setApplyTo}
            className={cls} shopName={shopName} canPick={canPick}
            label="Apply this payment to" placeholder="Apply this payment to…" withAdvance
          />
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
          {pickedBill ? (
            <p className="col-span-2 text-xs text-gray-500">The money goes to this bill&rsquo;s shop.</p>
          ) : (
            <PurchaseShopField
              value={moneyShop}
              onChange={setMoneyShop}
              label="Shop this money is for"
              emptyLabel={'The supplier\u2019s shop (by its bills)'}
              className="col-span-2"
              testId="money-shop"
            />
          )}
        </>}
        {kind === 'apply' && <>
          {advances !== null && advances.length === 0 ? (
            <p className="col-span-2 text-sm text-gray-600" data-testid="no-advances">This supplier has no unapplied advance.</p>
          ) : <>
            <select className={`${cls} col-span-2`} aria-label="Advance" value={advanceId} onChange={(e) => pickForApply(e.target.value, applyTo)}>
              <option value="">{advances === null ? 'Loading advances\u2026' : 'Pick the advance\u2026'}</option>
              {(advances ?? []).map((p) => (
                <option key={p.payment_id} value={p.payment_id}>
                  {inr(gross(p))} paid {(p.payment_date || '').slice(0, 10)}{p.reference ? ` \u00b7 ${p.reference}` : ''}
                  {canPick ? ` \u00b7 ${p.store_id ? `for ${shopName(p.store_id)}` : 'no shop on record'}` : ''}
                </option>
              ))}
            </select>
            <BillPicker
              bills={bills} failed={billsFailed} value={applyTo} onChange={(id) => pickForApply(advanceId, id)}
              className={cls} shopName={shopName} canPick={canPick}
              label="Bill to apply it to" placeholder="Pick the bill\u2026"
            />
            <label className="col-span-2 text-sm">Amount to apply
              <input className={cls} type="number" value={applyAmount} onChange={(e) => setApplyAmount(e.target.value)} />
            </label>
          </>}
        </>}
        {kind === 'debit' && <>
          <label className="col-span-2 text-sm">Credit type
            <select className={cls} value={creditType} onChange={event => setCreditType(event.target.value as typeof creditType)}>
              <option value="">Choose credit type…</option>
              <option value="RETURN_CN">Returned goods — confirm supplier note</option>
              <option value="SCHEME_CN">Scheme credit</option>
              <option value="DISCOUNT_CN">Discount credit</option>
              <option value="QUALITY_CN">Quality adjustment without returned goods</option>
            </select>
          </label>
          {creditType === 'RETURN_CN' ? <p className="col-span-2 text-sm text-gray-600">
            Use the matching vendor return to record the supplier’s actual credit note and original bill details.
          </p> : <>
          <input className={cls} type="number" placeholder="Amount" onChange={(e) => set('amount', e.target.value)} />
          <input className={cls} type="date" defaultValue={today} onChange={(e) => set('date', e.target.value)} />
          <input className={`${cls} col-span-2`} placeholder="Reason for adjustment" onChange={(e) => set('reason', e.target.value)} />
          <PurchaseShopField
            value={moneyShop}
            onChange={setMoneyShop}
            label="Shop this money is for"
            emptyLabel={'The supplier\u2019s shop (by its bills)'}
            className="col-span-2"
            testId="money-shop"
          />
          </>}
        </>}
      </div>
      <div className="flex justify-end mt-2">
        {kind === 'debit' && creditType === 'RETURN_CN' ? <Link
          to={`/purchase/vendor-returns?vendor_id=${encodeURIComponent(vendorId)}`}
          className="text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5"
        >Open Vendor Returns</Link> : <button type="button" onClick={save} disabled={saving} className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5 disabled:opacity-60">
          {saving && <Loader2 className="w-4 h-4 animate-spin" />} Save
        </button>}
      </div>
    </div>
  );
}

/** The toast after a bill / payment / debit note is saved: the shop the
 *  server booked it to (F63, R9) -- the shop picked, the receipt's or the
 *  bill's, or for an admin's money that names neither the shop the server
 *  worked out -- so the admin sees where it went. Null = the server stamped
 *  no shop, and the toast says so; no store_id at all (an older server) says
 *  nothing it does not know. */
function bookedTo(what: string, saved: { store_id?: string | null } | null | undefined, shopName: (id: string) => string) {
  const shop = saved?.store_id;
  if (shop) return `${what} recorded for ${shopName(shop)}`;
  if (shop === null) return `${what} recorded with no shop on record`;
  return 'Recorded';
}

/** This supplier's open bills to put money against (owner 2026-10-18: a
 *  payment picks its bill). An admin's list covers every shop, so each bill
 *  names its shop. `withAdvance` adds the "no bill yet" choice -- money the
 *  supplier keeps as an advance until Apply advance puts it on a bill. */
function BillPicker({ bills, failed, value, onChange, className, shopName, canPick, label, placeholder, withAdvance }: {
  bills: VendorBill[] | null;
  failed: boolean;
  value: string;
  onChange: (billId: string) => void;
  className: string;
  shopName: (id: string) => string;
  canPick: boolean;
  label: string;
  placeholder: string;
  withAdvance?: boolean;
}) {
  return (
    <div className="col-span-2">
      <select className={className} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">{bills === null ? 'Loading bills…' : placeholder}</option>
        {(bills ?? []).map((b) => (
          <option key={b.bill_id} value={b.bill_id}>
            Bill {b.bill_number} · {(b.bill_date || '').slice(0, 10)} · {inr(b.outstanding)} left
            {canPick ? ` · ${b.store_id ? `for ${shopName(b.store_id)}` : 'no shop on record'}` : ''}
          </option>
        ))}
        {withAdvance && <option value={ADVANCE}>Advance (no bill yet)</option>}
      </select>
      {failed ? (
        <p className="text-xs text-red-600 mt-1">Could not load this supplier&rsquo;s bills — try again in a moment.</p>
      ) : bills !== null && bills.length === 0 ? (
        <p className="text-xs text-gray-500 mt-1" data-testid="no-open-bills">No unpaid bills for this supplier.</p>
      ) : null}
    </div>
  );
}

/** The Bill form's goods-receipt list (owner ruling 15). The list is the
 *  server's Purchase shop scope: an admin's covers every shop, so each
 *  receipt names its shop and an empty list says it searched every store
 *  (the invoice pickers' wording); anyone else's is their own shop's. */
function ReceiptPicker({ receipts, failed, value, onChange, className, shopName }: {
  receipts: VendorReceiptRow[] | null;
  failed: boolean;
  value: string;
  onChange: (grnId: string) => void;
  className: string;
  shopName: (id: string) => string;
}) {
  const { canPick, ownStoreId } = usePurchaseShop();
  // The shop the server searched: none named = every store for an admin, the
  // caller's own shop for anyone else (a login with no shop: every store).
  const searched = canPick ? undefined : ownStoreId;
  return (
    <div className="col-span-2">
      <select className={className} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Goods receipt">
        <option value="">
          {receipts === null ? 'Loading receipts…' : 'Pick the goods receipt…'}
        </option>
        {(receipts ?? []).map((g) => (
          <option key={g.grn_id} value={g.grn_id}>
            {g.grn_number || g.grn_id}
            {g.grn_subtype === 'DELIVERY_CHALLAN' ? ` · DC ${g.dc_number || ''} ${g.dc_date || ''}` : g.vendor_invoice_no ? ` · inv ${g.vendor_invoice_no}` : ''}
            {searched ? '' : ` · ${g.store_id ? `for ${shopName(g.store_id)}` : 'no shop on record'}`}
          </option>
        ))}
      </select>
      {failed ? (
        <p className="text-xs text-red-600 mt-1">Could not load this vendor&rsquo;s receipts — try again in a moment.</p>
      ) : receipts !== null && receipts.length === 0 ? (
        <p className="text-xs text-amber-700 mt-1" data-testid="receipts-empty">
          No unbilled goods receipts for this vendor {searched ? <>at <PurchaseShopName storeId={searched} /></> : 'in any store'}. The goods are received first, by the
          shop&rsquo;s store manager. For a local dealer purchase they choose &lsquo;Bought without PO&rsquo;
          on Goods Receipt and pick the supplier, adding a new dealer if needed. A supplier&rsquo;s delivery
          challan is logged as a Delivery Challan. Then book the bill from Purchase &rarr; Invoices &rarr;
          Create from GRN, or the &lsquo;Book the bill&rsquo; task. GST credit is available only from a GST-registered dealer.
        </p>
      ) : null}
    </div>
  );
}
