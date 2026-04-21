// ============================================================================
// IMS 2.0 - POS Payment Step
// ============================================================================
// Extracted from POSLayout.tsx — payment collection step with split payments,
// EMI, cash change calculator, voucher/credit billing options.
//
// Phase 6.6: Visual cleanup — replaced dark-theme remnants
// (bg-white/900, text-gray-700/400, *-900/30 alpha overlays) with the
// app's light-theme tokens. Pure visual change, no logic touched.

import { useState } from 'react';
import {
  IndianRupee, Phone, CreditCard, FileText,
  CheckCircle, X,
} from 'lucide-react';
import { usePOSStore } from '../../stores/posStore';
import { CreditBillingOption } from './CreditBillingOption';
import { VoucherRedemption } from './VoucherRedemption';

/** Safe currency format */
function fc(amount: number | undefined | null): string {
  const val = Math.round((amount || 0) * 100) / 100;
  return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

// ============================================================================
// Cash Change Calculator
// ============================================================================
function CashChangeCalculator({ grandTotal }: { grandTotal: number; totalPaid: number }) {
  const [cashTendered, setCashTendered] = useState('');
  const tendered = parseFloat(cashTendered) || 0;
  const change = tendered - grandTotal;
  const quickAmounts = [
    Math.ceil(grandTotal / 100) * 100,
    Math.ceil(grandTotal / 500) * 500,
    Math.ceil(grandTotal / 1000) * 1000,
    Math.ceil(grandTotal / 2000) * 2000,
  ].filter((v, i, a) => v >= grandTotal && a.indexOf(v) === i).slice(0, 3);

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <p className="text-sm font-medium text-gray-700">Cash Tendered</p>
      <div className="flex gap-2 items-center">
        <span className="text-gray-500 text-lg">{'₹'}</span>
        <input type="number" value={cashTendered} onChange={(e) => setCashTendered(e.target.value)}
          onFocus={(e) => e.target.select()} placeholder={String(Math.round(grandTotal))}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-lg font-semibold text-center text-gray-900" />
      </div>
      <div className="flex gap-2">
        {quickAmounts.map(amt => (
          <button key={amt} onClick={() => setCashTendered(String(amt))}
            className="px-3 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs font-medium text-gray-700 hover:bg-gray-100">{fc(amt)}</button>
        ))}
      </div>
      {tendered > 0 && (
        <div className={`text-center py-2 rounded-lg font-bold text-lg ${change >= 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {change >= 0 ? `Change: ₹${Math.round(change).toLocaleString('en-IN')}` : `Short: ₹${Math.round(Math.abs(change)).toLocaleString('en-IN')}`}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// StepPayment
// ============================================================================
export function StepPayment() {
  const store = usePOSStore();
  const total = store.getGrandTotal(); const paid = store.getTotalPaid(); const balance = Math.round((total - paid) * 100) / 100;
  const [payMethod, setPayMethod] = useState<'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'EMI'>('CASH');
  const [payAmount, setPayAmount] = useState(''); const [payRef, setPayRef] = useState('');

  // EMI state
  const [showEMIForm, setShowEMIForm] = useState(false);
  const [emiProvider, setEmiProvider] = useState('HDFC');
  const [emiTenure, setEmiTenure] = useState(12);
  const [emiDownPayment, setEmiDownPayment] = useState('');

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
    const principal = balance - downPayment;
    // EMI rate fetched from store settings; falls back to 12% annual
    const annualRate = (store as any).emiAnnualRate ?? 0.12;
    const monthlyRate = annualRate / 12;
    const monthlyEMI = calculateEMI(principal, monthlyRate, emiTenure);
    const processingFee = (principal * 0.02);
    store.addPayment({
      method: 'EMI',
      amount: downPayment,
      reference: emiProvider,
      emiProvider,
      emiTenure,
      downPayment,
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
      <div className="bg-white border border-gray-200 rounded-xl p-6 text-center">
        <p className="text-sm text-gray-500 mb-1">{store.is_advance_payment ? 'Advance Due' : 'Total Due (incl. GST)'}</p>
        <p className="text-4xl font-bold text-gray-900">{'₹'}{Math.round(total).toLocaleString('en-IN')}</p>
        {paid > 0 && <div className="mt-3 flex justify-center gap-6 text-sm">
          <span className="text-green-600">Paid: {'₹'}{Math.round(paid).toLocaleString('en-IN')}</span>
          <span className={balance > 0 ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}>Balance: {'₹'}{Math.round(Math.max(0, balance)).toLocaleString('en-IN')}</span>
        </div>}
      </div>

      {/* Loyalty Points & Credit Billing Options */}
      {store.customer && !store.customer.id?.toString().startsWith('walkin-') && (
        <div className="space-y-3">
          <CreditBillingOption />
          <VoucherRedemption />
        </div>
      )}

      <div className="grid grid-cols-5 gap-2">
        {methods.map(m => (
          <button key={m.id} onClick={() => {
            if (m.id === 'CASH') {
              store.addPayment({ method: m.id, amount: Math.round((balance > 0 ? balance : total) * 100) / 100 });
            } else if (m.id === 'EMI') {
              setShowEMIForm(true);
            } else {
              setPayMethod(m.id);
              setPayAmount(String(Math.round((balance > 0 ? balance : total) * 100) / 100));
              setPayRef('');
            }
          }} disabled={balance <= 0}
            className={`flex flex-col items-center gap-1 p-3 rounded-xl border-2 transition-all ${balance <= 0 ? 'opacity-40 border-gray-200 text-gray-500' : 'border-gray-200 text-gray-700 hover:border-bv-red-300 hover:bg-bv-red-50'}`}>
            <m.icon className="w-6 h-6" /><span className="text-xs font-medium">{m.id === 'CASH' ? 'Full Cash' : m.id === 'EMI' ? 'EMI' : `${m.label} →`}</span>
          </button>
        ))}
      </div>

      {balance > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          <p className="text-sm font-medium text-gray-700">Split payment</p>
          <div className="flex gap-2">
            {methods.map(m => <button key={m.id} onClick={() => setPayMethod(m.id)} className={`px-3 py-1.5 rounded-lg text-xs font-medium ${payMethod === m.id ? 'bg-bv-red-600 text-gray-900' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>{m.label}</button>)}
          </div>
          <div className="flex gap-2">
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
              store.addPayment({ method: payMethod, amount: Math.min(a, balance), reference: payRef.trim() || undefined });
              setPayAmount(''); setPayRef('');
            }}
              disabled={!payAmount || parseFloat(payAmount) <= 0 || (payMethod !== 'CASH' && !payRef.trim())}
              className={`px-4 py-2 rounded-lg text-sm font-semibold ${
                !payAmount || parseFloat(payAmount) <= 0 || (payMethod !== 'CASH' && !payRef.trim())
                  ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'bg-bv-red-600 text-gray-900 hover:bg-bv-red-700'
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
              <select value={emiProvider} onChange={(e) => setEmiProvider(e.target.value)} className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900">
                {emiProviders.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700">Tenure (months)</label>
              <select value={emiTenure} onChange={(e) => setEmiTenure(Number(e.target.value))} className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900">
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
                <div className="flex justify-between"><span className="text-gray-700">Processing Fee (2%):</span><span className="font-semibold text-gray-900">{'₹'}{Math.round(((balance - (parseFloat(emiDownPayment) || 0)) * 0.02) * 100) / 100}</span></div>
                <div className="flex justify-between"><span className="text-gray-700">Monthly EMI ({emiTenure}m):</span><span className="font-bold text-blue-700">{'₹'}{Math.round(calculateEMI(balance - (parseFloat(emiDownPayment) || 0), 0.01, emiTenure) * 100) / 100}</span></div>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => {
                setShowEMIForm(false);
                setEmiDownPayment('');
              }} className="flex-1 px-4 py-2 rounded-lg text-sm font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200">Cancel</button>
              <button onClick={handleEMISubmit} disabled={!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance} className={`flex-1 px-4 py-2 rounded-lg text-sm font-semibold ${!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'bg-bv-red-600 text-gray-900 hover:bg-bv-red-700'}`}>Add EMI</button>
            </div>
          </div>
        </div>
      )}

      {(store.payments || []).length > 0 && <div className="space-y-2">
        {(store.payments || []).map((p, i) => (
          <div key={i} className="flex items-center justify-between bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm">
            <div className="flex items-center gap-2"><CheckCircle className="w-4 h-4 text-green-600" /><span className="font-medium text-gray-900">{p.method}</span>{p.reference && <span className="text-gray-500">({p.reference})</span>}</div>
            <div className="flex items-center gap-2"><span className="font-semibold text-gray-900">{'₹'}{Math.round(p.amount * 100 / 100).toLocaleString('en-IN')}</span><button onClick={() => store.removePayment(i)} className="text-gray-500 hover:text-red-500"><X className="w-4 h-4" /></button></div>
          </div>
        ))}
      </div>}

      {/* Cash change calculator — only if cash payment was added */}
      {(store.payments || []).some(p => p.method === 'CASH') && balance <= 0 && (
        <CashChangeCalculator grandTotal={total} totalPaid={paid} />
      )}

      {balance <= 0 && <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center text-green-700 font-semibold">Payment complete — click "Complete Order" to finalize</div>}
    </div>
  );
}

export default StepPayment;
