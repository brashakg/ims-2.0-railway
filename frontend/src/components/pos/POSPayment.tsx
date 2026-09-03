// ============================================================================
// IMS 2.0 - POS Payment Step
// ============================================================================
// Extracted from POSLayout.tsx — payment collection step with split payments,
// EMI, cash change calculator, voucher/credit billing options.
//
// Phase 6.6: Visual cleanup — replaced dark-theme remnants
// (bg-white/900, text-gray-700/400, *-900/30 alpha overlays) with the
// app's light-theme tokens. Pure visual change, no logic touched.

import { useEffect, useState } from 'react';
import {
  IndianRupee, Phone, CreditCard, FileText,
  CheckCircle, X, ChevronDown, ChevronUp,
} from 'lucide-react';
import { storeApi } from '../../services/api';
import { usePOSStore, type CashTenderCapture, type PaymentEntry } from '../../stores/posStore';
import { CreditBillingOption } from './CreditBillingOption';
import { VoucherRedemption } from './VoucherRedemption';
import { LoyaltyRedeemControl } from './LoyaltyRedeemControl';
import DenominationGrid from '../cash/DenominationGrid';
import { blankDenoms, hasCount, setPieces as setRowPieces, type DenomRow } from '../../utils/denominations';

// Fallback while the policy fetch is in flight / failed. MIRRORS the backend
// registry default for `pos.emi_annual_rate_percent` (backend
// api/services/policy_registry.py) -- change both together.
const EMI_ANNUAL_RATE_PERCENT_FALLBACK = 12;

/** Safe currency format */
function fc(amount: number | undefined | null): string {
  const val = Math.round((amount || 0) * 100) / 100;
  return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

// ============================================================================
// Cash Change Calculator
// ============================================================================
// Owner ruling 2026-08-25: the tendered figure (and an OPTIONAL note-by-note
// grid) is now RECORDED on the sale -- it feeds posStore.cash_tender, which
// POSLayout attaches to the CASH payment leg the backend already reads
// (orders.py cash_leg_record). The change arithmetic DISPLAYED here is
// untouched, the payment amounts are untouched, and skipping everything still
// completes the sale exactly as before (the record lands as NOT_CAPTURED).
// `cashDue` is the CASH LEG, not the bill. Getting that wrong is what produced
// the owner-reported nonsense: UPI 590 + CARD 12,000 + CASH 28,000 on a 40,590
// bill, 29,000 cash on the counter, and the screen shouting "Short: 11,590"
// (= 40,590 - 29,000). The customer was not short a rupee - 29,000 handed over
// against a 28,000 cash leg is 1,000 CHANGE. Telling a cashier to collect
// another 11,590 that has already been paid by card and UPI is the worst
// possible direction for this error to point.
function CashChangeCalculator({
  cashDue,
  onCapture,
}: {
  cashDue: number;
  /** Where the capture goes when this till is not billing the cart. */
  onCapture?: (capture: CashTenderCapture | null) => void;
}) {
  const storeSetCashTender = usePOSStore((s) => s.setCashTender);
  const setCashTender = onCapture ?? storeSetCashTender;
  const [cashTendered, setCashTendered] = useState('');
  const [noteRows, setNoteRows] = useState<DenomRow[]>(blankDenoms());
  const [showNotes, setShowNotes] = useState(false);
  const tendered = parseFloat(cashTendered) || 0;
  const change = tendered - cashDue;
  const quickAmounts = [
    Math.ceil(cashDue / 100) * 100,
    Math.ceil(cashDue / 500) * 500,
    Math.ceil(cashDue / 1000) * 1000,
    Math.ceil(cashDue / 2000) * 2000,
  ].filter((v, i, a) => v >= cashDue && a.indexOf(v) === i).slice(0, 3);

  // Mirror what was typed/tapped into the store so Complete Order can attach
  // it to the CASH leg. Blank tendered + untouched grid -> null (NOT_CAPTURED
  // on the server -- never a fabricated zero).
  const publish = (amountStr: string, rows: DenomRow[]) => {
    const amt = parseFloat(amountStr) || 0;
    const counted = hasCount(rows);
    if (amt > 0 || counted) {
      setCashTender({ tendered_amount: amt, rows: counted ? rows.filter((r) => r.pieces > 0) : undefined });
    } else {
      setCashTender(null);
    }
  };
  const onTendered = (v: string) => { setCashTendered(v); publish(v, noteRows); };
  const onNotePieces = (i: number, pieces: number) => {
    setNoteRows((rows) => {
      const next = setRowPieces(rows, i, pieces);
      publish(cashTendered, next);
      return next;
    });
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <p className="text-sm font-medium text-gray-700">Cash Tendered</p>
      <div className="flex gap-2 items-center">
        <span className="text-gray-500 text-lg">{'₹'}</span>
        <input type="number" value={cashTendered} onChange={(e) => onTendered(e.target.value)}
          onFocus={(e) => e.target.select()} placeholder={String(Math.round(cashDue))}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-lg font-semibold text-center text-gray-900" />
      </div>
      <div className="flex gap-2">
        {quickAmounts.map(amt => (
          <button key={amt} onClick={() => onTendered(String(amt))}
            className="px-3 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs font-medium text-gray-700 hover:bg-gray-100">{fc(amt)}</button>
        ))}
      </div>
      {tendered > 0 && (
        <div className={`text-center py-2 rounded-lg font-bold text-lg ${change >= 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {change >= 0 ? `Change: ₹${Math.round(change).toLocaleString('en-IN')}` : `Short: ₹${Math.round(Math.abs(change)).toLocaleString('en-IN')}`}
        </div>
      )}
      {/* Optional: WHICH notes came over the counter. Collapsed by default so
          the fast path costs nothing; skipping it never blocks the sale. */}
      <button type="button" onClick={() => setShowNotes((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-700">
        {showNotes ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        Count the notes received (optional)
      </button>
      {showNotes && (
        <DenominationGrid rows={noteRows} onChange={onNotePieces} />
      )}
    </div>
  );
}

// ============================================================================
// PaymentTarget — the seam that lets a NON-CART surface reuse this till
// ============================================================================
// Pass nothing and every line below behaves EXACTLY as the two live tills
// (/pos/new, /pos/counter) already render it: the cart is the target.
//
// The delivery counter is not a fresh sale — it collects the BALANCE of an
// order that already exists on the server — so it hands in its own due figure
// and its own payment list instead of the cart's. Everything that makes this a
// real till (split tender, per-leg reference, EMI, the leg list, the cash
// denomination capture) is then the SAME code on all three surfaces.
//
// What target mode deliberately does NOT render: loyalty / store credit /
// voucher. Those three read `store.customer` and `store.getGrandTotal()` and
// park their intent ON THE CART for submitOrder to consume — so on a non-cart
// surface they would spend the wrong customer's points against the wrong
// amount and leak into the next sale at the till. They stay cart-only until
// they grow a target of their own.
export interface PaymentTarget {
  /** What is owed on the thing being paid — an existing order's balance_due. */
  due: number;
  payments: PaymentEntry[];
  addPayment: (payment: Omit<PaymentEntry, 'timestamp'>) => void;
  removePayment: (index: number) => void;
  /** Where the note-by-note cash capture goes. Omitted -> not recorded. */
  setCashTender?: (capture: CashTenderCapture | null) => void;
  /** Store whose EMI rate applies. Defaults to the cart's store. */
  storeId?: string;
}

// ============================================================================
// StepPayment
// ============================================================================
export function StepPayment({ target }: { target?: PaymentTarget } = {}) {
  const store = usePOSStore();
  const payments = target ? target.payments : store.payments;
  const addPayment = target ? target.addPayment : store.addPayment;
  const removePayment = target ? target.removePayment : store.removePayment;
  const total = target ? target.due : store.getGrandTotal();
  // Same arithmetic as the store's getTotalPaid — a plain sum of the legs.
  const paid = target
    ? (target.payments || []).reduce((s, p) => s + (Number(p.amount) || 0), 0)
    : store.getTotalPaid();
  const balance = Math.round((total - paid) * 100) / 100;
  const storeId = target?.storeId || store.store_id;
  const [payMethod, setPayMethod] = useState<'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'EMI'>('CASH');
  // Target mode opens with the amount due already typed in: at the delivery
  // counter "collect the balance" is the common case, and the method row —
  // which is what prefills in cart mode — already sits on CASH.
  const [payAmount, setPayAmount] = useState(
    target ? String(Math.round(target.due * 100) / 100) : '',
  );
  const [payRef, setPayRef] = useState('');

  // EMI state
  const [showEMIForm, setShowEMIForm] = useState(false);
  const [showCredit, setShowCredit] = useState(false);
  const [showVoucher, setShowVoucher] = useState(false);
  const [emiProvider, setEmiProvider] = useState('HDFC');
  const [emiTenure, setEmiTenure] = useState(12);
  const [emiDownPayment, setEmiDownPayment] = useState('');
  // The rate the BACKEND will apply, read off the STORE DETAIL -- the one
  // endpoint every POS role already has (GET /stores/{id} is AUTHENTICATED +
  // store-scoped, and the server stamps `emi_annual_rate_percent` on it via
  // the SAME shared resolver the order add-payment engine uses, so quote and
  // charge cannot drift). The policies endpoint is deliberately NOT used
  // here: it is closed to SALES_CASHIER/SALES_STAFF, and the first cut of
  // this fix fetched it anyway -- the 403 died in a silent catch and every
  // cashier quoted the fallback while the order charged the configured rate,
  // which is the exact defect this change exists to close.
  const [emiAnnualRatePct, setEmiAnnualRatePct] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    if (!storeId) return () => { alive = false; };
    storeApi
      .getStore(storeId)
      .then((detail: { emi_annual_rate_percent?: number }) => {
        const v = Number(detail?.emi_annual_rate_percent);
        if (alive && Number.isFinite(v)) setEmiAnnualRatePct(v);
      })
      .catch(() => { /* keep fallback -- it mirrors the registry default */ });
    return () => { alive = false; };
  }, [storeId]);
  const annualRatePct = emiAnnualRatePct ?? EMI_ANNUAL_RATE_PERCENT_FALLBACK;

  const emiProviders = ['HDFC', 'ICICI', 'AXIS', 'ADITYA BIRLA', 'BAJAJ', 'INDIABULLS'];
  const emiTenures = [3, 6, 9, 12, 18, 24];

  const calculateEMI = (principal: number, monthlyRate: number, months: number) => {
    if (monthlyRate === 0) return principal / months;
    const numerator = principal * monthlyRate * Math.pow(1 + monthlyRate, months);
    const denominator = Math.pow(1 + monthlyRate, months) - 1;
    return numerator / denominator;
  };

  const handleEMISubmit = () => {
    const downPayment = parseFloat(emiDownPayment) || 0;
    if (downPayment < 0 || downPayment >= balance) return;
    // POS-2: emiBalance is the financed amount (loan principal).
    // `amount` on the payment entry is the down-payment collected NOW
    // (which reduces balance_due); emiBalance is forwarded to the backend
    // as emi_principal so the schedule is computed on the correct base.
    const emiBalance = balance - downPayment;
    const monthlyRate = annualRatePct / 100 / 12;
    const monthlyEMI = calculateEMI(emiBalance, monthlyRate, emiTenure);
    const processingFee = (emiBalance * 0.02);
    addPayment({
      method: 'EMI',
      amount: downPayment,
      reference: emiProvider,
      emiProvider,
      emiTenure,
      downPayment,
      emiBalance,
      monthlyEMI: Math.round(monthlyEMI * 100) / 100,
      processingFee: Math.round(processingFee * 100) / 100,
    });
    setShowEMIForm(false);
    setEmiDownPayment('');
  };

  const methods = [
    { id: 'CASH' as const, label: 'Cash', icon: IndianRupee },
    { id: 'UPI' as const, label: 'UPI', icon: Phone },
    { id: 'CARD' as const, label: 'Card', icon: CreditCard },
    { id: 'BANK_TRANSFER' as const, label: 'Bank', icon: FileText },
    { id: 'EMI' as const, label: 'EMI', icon: CreditCard },
  ];

  return (
    <div className="w-full max-w-2xl mx-auto space-y-4">
      {/* The owning surface prints the headline figure in target mode (the
          delivery counter shows the ORDER's balance, with what is already
          paid against the order total), so this card would only repeat it. */}
      {!target && <div className="bg-white border border-gray-200 rounded-xl p-6 text-center">
        <p className="text-sm text-gray-500 mb-1">{store.is_advance_payment ? 'Advance Due' : 'Total Due (incl. GST)'}</p>
        <p className="figure text-4xl text-gray-900">{'₹'}{Math.round(total).toLocaleString('en-IN')}</p>
        {paid > 0 && <div className="mt-3 flex justify-center gap-6 text-sm">
          <span className="figure text-green-600">Paid: {'₹'}{Math.round(paid).toLocaleString('en-IN')}</span>
          <span className={balance > 0 ? 'figure text-red-600' : 'figure text-green-600'}>Balance: {'₹'}{Math.round(Math.max(0, balance)).toLocaleString('en-IN')}</span>
        </div>}
      </div>}

      {/* Loyalty Points & Credit Billing Options — CART ONLY, see PaymentTarget */}
      {!target && store.customer && !store.customer.id?.toString().startsWith('walkin-') && (
        <div className="space-y-2">
          <LoyaltyRedeemControl />
          {/* Store credit and vouchers apply to a minority of bills but were
              costing two full cards of till height on every single one. They
              open on a tap and stay open once used, so a sale that needs them
              is one click away and a sale that does not never sees them. */}
          <div className="flex gap-2">
            {([
              ['credit', 'Store credit', showCredit, setShowCredit] as const,
              ['voucher', 'Voucher / gift card', showVoucher, setShowVoucher] as const,
            ]).map(([key, label, open, set]) => (
              <button
                key={key}
                type="button"
                onClick={() => set(!open)}
                aria-expanded={open}
                className={`flex-1 px-3 py-2 rounded-lg border text-xs font-medium min-h-[40px] ${
                  open
                    ? 'border-bv-red-300 bg-bv-red-50 text-bv-red-700'
                    : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                }`}
              >
                {open ? `Hide ${label.toLowerCase()}` : label}
              </button>
            ))}
          </div>
          {showCredit && <CreditBillingOption />}
          {showVoucher && <VoucherRedemption />}
        </div>
      )}

      {balance > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          {/* ONE payment card, not two. The 5-button grid above this said the
              same thing in bigger type -- pick a tender -- and the only thing
              it could do that this cannot was "full cash in one tap", which is
              now the prefilled amount. Two controls for one decision is how a
              till gets tall enough to scroll. */}
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-gray-700">Payment method</p>
            <p className="text-xs text-gray-500">
              Balance {'₹'}{Math.round(balance).toLocaleString('en-IN')}
            </p>
          </div>
          <div className="flex gap-2 flex-wrap">
            {methods.map(m => (
              <button
                key={m.id}
                onClick={() => {
                  if (m.id === 'EMI') { setShowEMIForm(true); return; }
                  setShowEMIForm(false);
                  setPayMethod(m.id);
                  // Prefill the whole remaining balance: paying it off in full
                  // is the common case, so it should be one more tap, not a
                  // typed number. Editable for a genuine split.
                  setPayAmount(String(Math.round(balance * 100) / 100));
                  setPayRef('');
                }}
                className={`px-3 py-2 rounded-lg text-xs font-medium min-h-[40px] ${
                  (m.id === 'EMI' ? showEMIForm : payMethod === m.id && !showEMIForm)
                    ? 'bg-bv-red-600 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className={`flex gap-2 ${showEMIForm ? 'hidden' : ''}`}>
            <input type="number" min="1" max={balance} step="0.01" value={payAmount}
              onChange={(e) => setPayAmount(e.target.value)}
              onFocus={(e) => e.target.select()}
              placeholder={`Amount (max ₹${Math.round(balance).toLocaleString('en-IN')})`} className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900" />
            {payMethod !== 'CASH' && <input value={payRef} onChange={(e) => setPayRef(e.target.value)} placeholder={payMethod === 'UPI' ? 'UPI Txn ID *' : payMethod === 'CARD' ? 'Approval code' : 'Reference'} className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900" />}
            <button onClick={() => {
              const a = parseFloat(payAmount);
              if (!a || a <= 0) return;
              if (a > balance + 0.01) { setPayAmount(String(Math.ceil(balance * 100) / 100)); return; }
              if (payMethod !== 'CASH' && !payRef.trim()) return; // Require ref for non-cash
              addPayment({ method: payMethod, amount: Math.min(a, balance), reference: payRef.trim() || undefined });
              setPayAmount(''); setPayRef('');
            }}
              disabled={!payAmount || parseFloat(payAmount) <= 0 || (payMethod !== 'CASH' && !payRef.trim())}
              className={`px-4 py-2 rounded-lg text-sm font-semibold ${
                !payAmount || parseFloat(payAmount) <= 0 || (payMethod !== 'CASH' && !payRef.trim())
                  ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'bg-bv-red-600 text-white hover:bg-bv-red-700'
              }`}>Add</button>
          </div>
          {payMethod !== 'CASH' && !payRef.trim() && payAmount && <p className="text-xs text-amber-600">Reference/Txn ID required for {payMethod}</p>}
        </div>
      )}

      {showEMIForm && balance > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          <p className="text-sm font-medium text-gray-700">EMI Details</p>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-gray-700">EMI Provider</label>
              <select aria-label="EMI Provider" value={emiProvider} onChange={(e) => setEmiProvider(e.target.value)} className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900">
                {emiProviders.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700">Tenure (months)</label>
              <select aria-label="Tenure (months)" value={emiTenure} onChange={(e) => setEmiTenure(Number(e.target.value))} className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900">
                {emiTenures.map(t => <option key={t} value={t}>{t} months</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700">Down Payment</label>
              <input type="number" min="0" max={balance - 0.01} step="100" value={emiDownPayment} onChange={(e) => setEmiDownPayment(e.target.value)} placeholder={`Max ₹${Math.round(balance).toLocaleString('en-IN')}`} className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900" onFocus={(e) => e.target.select()} />
            </div>
            {emiDownPayment && (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-gray-700">Loan Amount:</span><span className="font-semibold text-gray-900">{'₹'}{Math.round((balance - (parseFloat(emiDownPayment) || 0)) * 100) / 100}</span></div>
                <div className="flex justify-between"><span className="text-gray-700">Interest Rate:</span><span className="font-semibold text-gray-900">{annualRatePct}% p.a.</span></div>
                <div className="flex justify-between"><span className="text-gray-700">Processing Fee (2%):</span><span className="font-semibold text-gray-900">{'₹'}{Math.round(((balance - (parseFloat(emiDownPayment) || 0)) * 0.02) * 100) / 100}</span></div>
                <div className="flex justify-between"><span className="text-gray-700">Monthly EMI ({emiTenure}m):</span><span className="font-bold text-blue-700" data-testid="emi-monthly-quote">{'₹'}{Math.round(calculateEMI(balance - (parseFloat(emiDownPayment) || 0), annualRatePct / 100 / 12, emiTenure) * 100) / 100}</span></div>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => {
                setShowEMIForm(false);
                setEmiDownPayment('');
              }} className="flex-1 px-4 py-2 rounded-lg text-sm font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200">Cancel</button>
              <button onClick={handleEMISubmit} disabled={!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance} className={`flex-1 px-4 py-2 rounded-lg text-sm font-semibold ${!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'bg-bv-red-600 text-white hover:bg-bv-red-700'}`}>Add EMI</button>
            </div>
          </div>
        </div>
      )}

      {(payments || []).length > 0 && <div className="space-y-2">
        {(payments || []).map((p, i) => (
          <div key={i} className="bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <CheckCircle className="w-4 h-4 text-green-600" />
                <span className="font-medium text-gray-900">{p.method}</span>
                {p.reference && <span className="text-gray-500">({p.reference})</span>}
              </div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-gray-900">
                  {'₹'}{(Math.round(p.amount * 100) / 100).toLocaleString('en-IN')}
                </span>
                <button onClick={() => removePayment(i)} aria-label="Remove payment" title="Remove payment" className="text-gray-500 hover:text-red-500"><X className="w-4 h-4" /></button>
              </div>
            </div>
            {/* POS-2: show EMI financed balance + monthly installment below the down-payment row */}
            {p.method === 'EMI' && p.emiBalance && p.emiBalance > 0 && (
              <div className="mt-1 pl-6 text-xs text-gray-500 space-y-0.5">
                <div>Loan: {'₹'}{p.emiBalance.toLocaleString('en-IN')} over {p.emiTenure}m via {p.emiProvider}</div>
                {p.monthlyEMI && <div>Monthly EMI: {'₹'}{p.monthlyEMI.toLocaleString('en-IN')}</div>}
              </div>
            )}
          </div>
        ))}
      </div>}

      {/* Cash change calculator — only if cash payment was added. It is scoped
          to the CASH LEG: on a split bill the other tenders are already
          settled, so comparing the notes on the counter against the WHOLE bill
          reports a shortfall that does not exist. */}
      {(payments || []).some(p => p.method === 'CASH') && balance <= 0 && (
        <CashChangeCalculator
          onCapture={target?.setCashTender}
          cashDue={
            Math.round(
              (payments || [])
                .filter((p) => p.method === 'CASH')
                .reduce((sum, p) => sum + (Number(p.amount) || 0), 0) * 100,
            ) / 100
          }
        />
      )}

      {!target && balance <= 0 && <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center text-green-700 font-semibold">Payment complete — click "Complete Order" to finalize</div>}
    </div>
  );
}

export default StepPayment;
