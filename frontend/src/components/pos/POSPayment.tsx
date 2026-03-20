// ============================================================================
// IMS 2.0 - POS Payment Step
// ============================================================================
// Extracted from POSLayout.tsx — payment collection step with split payments,
// EMI, cash change calculator, voucher/credit billing options.

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
  return `\u20B9${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
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
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 space-y-3">
      <p className="text-sm font-medium text-gray-300">Cash Tendered</p>
      <div className="flex gap-2 items-center">
        <span className="text-gray-400 text-lg">{'\u20B9'}</span>
        <input type="number" value={cashTendered} onChange={(e) => setCashTendered(e.target.value)}
          onFocus={(e) => e.target.select()} placeholder={String(Math.round(grandTotal))}
          className="flex-1 px-3 py-2 border border-gray-600 rounded-lg text-lg font-semibold text-center" />
      </div>
      <div className="flex gap-2">
        {quickAmounts.map(amt => (
          <button key={amt} onClick={() => setCashTendered(String(amt))}
            className="px-3 py-1 bg-gray-800 border border-gray-700 rounded-lg text-xs font-medium hover:bg-gray-700">{fc(amt)}</button>
        ))}
      </div>
      {tendered > 0 && (
        <div className={`text-center py-2 rounded-lg font-bold text-lg ${change >= 0 ? 'bg-green-900/30 text-green-700' : 'bg-red-900/30 text-red-600'}`}>
          {change >= 0 ? `Change: \u20B9${Math.round(change).toLocaleString('en-IN')}` : `Short: \u20B9${Math.round(Math.abs(change)).toLocaleString('en-IN')}`}
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
    const annualRate = 0.12; // 12% annual
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
    <div className="max-w-xl mx-auto space-y-4">
      <div className="bg-gray-800 border border-gray-700 rounded-xl p-6 text-center">
        <p className="text-sm text-gray-500 mb-1">{store.is_advance_payment ? 'Advance Due' : 'Total Due (incl. GST)'}</p>
        <p className="text-4xl font-bold text-white">{'\u20B9'}{Math.round(total).toLocaleString('en-IN')}</p>
        {paid > 0 && <div className="mt-3 flex justify-center gap-6 text-sm">
          <span className="text-green-600">Paid: {'\u20B9'}{Math.round(paid).toLocaleString('en-IN')}</span>
          <span className={balance > 0 ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}>Balance: {'\u20B9'}{Math.round(Math.max(0, balance)).toLocaleString('en-IN')}</span>
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
            className={`flex flex-col items-center gap-1 p-3 rounded-xl border-2 transition-all ${balance <= 0 ? 'opacity-40 border-gray-700' : 'border-gray-700 hover:border-bv-gold-300 hover:bg-bv-gold-900/30'}`}>
            <m.icon className="w-6 h-6 text-gray-300" /><span className="text-xs font-medium">{m.id === 'CASH' ? 'Full Cash' : m.id === 'EMI' ? 'EMI' : `${m.label} \u2192`}</span>
          </button>
        ))}
      </div>

      {balance > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 space-y-3">
          <p className="text-sm font-medium text-gray-300">Split payment</p>
          <div className="flex gap-2">
            {methods.map(m => <button key={m.id} onClick={() => setPayMethod(m.id)} className={`px-3 py-1.5 rounded-lg text-xs font-medium ${payMethod === m.id ? 'bg-bv-gold-500 text-white' : 'bg-gray-700 text-gray-300'}`}>{m.label}</button>)}
          </div>
          <div className="flex gap-2">
            <input type="number" min="1" max={balance} step="0.01" value={payAmount}
              onChange={(e) => setPayAmount(e.target.value)}
              onFocus={(e) => e.target.select()}
              placeholder={`Amount (max \u20B9${Math.round(balance).toLocaleString('en-IN')})`} className="flex-1 px-3 py-2 border border-gray-600 rounded-lg text-sm" />
            {payMethod !== 'CASH' && <input value={payRef} onChange={(e) => setPayRef(e.target.value)} placeholder={payMethod === 'UPI' ? 'UPI Txn ID *' : payMethod === 'CARD' ? 'Approval code' : 'Reference'} className="flex-1 px-3 py-2 border border-gray-600 rounded-lg text-sm" />}
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
                  ? 'bg-gray-700 text-gray-400 cursor-not-allowed' : 'bg-bv-gold-500 text-white hover:bg-bv-gold-600'
              }`}>Add</button>
          </div>
          {payMethod !== 'CASH' && !payRef.trim() && payAmount && <p className="text-xs text-amber-600">Reference/Txn ID required for {payMethod}</p>}
        </div>
      )}

      {showEMIForm && balance > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 space-y-3">
          <p className="text-sm font-medium text-gray-300">EMI Details</p>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-gray-300">EMI Provider</label>
              <select value={emiProvider} onChange={(e) => setEmiProvider(e.target.value)} className="w-full mt-1 px-3 py-2 border border-gray-600 rounded-lg text-sm">
                {emiProviders.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-300">Tenure (months)</label>
              <select value={emiTenure} onChange={(e) => setEmiTenure(Number(e.target.value))} className="w-full mt-1 px-3 py-2 border border-gray-600 rounded-lg text-sm">
                {emiTenures.map(t => <option key={t} value={t}>{t} months</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-300">Down Payment</label>
              <input type="number" min="0" max={balance - 0.01} step="100" value={emiDownPayment} onChange={(e) => setEmiDownPayment(e.target.value)} placeholder={`Max \u20B9${Math.round(balance).toLocaleString('en-IN')}`} className="w-full mt-1 px-3 py-2 border border-gray-600 rounded-lg text-sm" onFocus={(e) => e.target.select()} />
            </div>
            {emiDownPayment && (
              <div className="bg-blue-900/30 border border-blue-200 rounded-lg p-3 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-gray-300">Loan Amount:</span><span className="font-semibold">{'\u20B9'}{Math.round((balance - (parseFloat(emiDownPayment) || 0)) * 100) / 100}</span></div>
                <div className="flex justify-between"><span className="text-gray-300">Processing Fee (2%):</span><span className="font-semibold">{'\u20B9'}{Math.round(((balance - (parseFloat(emiDownPayment) || 0)) * 0.02) * 100) / 100}</span></div>
                <div className="flex justify-between"><span className="text-gray-300">Monthly EMI ({emiTenure}m):</span><span className="font-bold text-blue-700">{'\u20B9'}{Math.round(calculateEMI(balance - (parseFloat(emiDownPayment) || 0), 0.01, emiTenure) * 100) / 100}</span></div>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => {
                setShowEMIForm(false);
                setEmiDownPayment('');
              }} className="flex-1 px-4 py-2 rounded-lg text-sm font-semibold bg-gray-700 text-gray-300 hover:bg-gray-300">Cancel</button>
              <button onClick={handleEMISubmit} disabled={!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance} className={`flex-1 px-4 py-2 rounded-lg text-sm font-semibold ${!emiDownPayment || parseFloat(emiDownPayment) < 0 || parseFloat(emiDownPayment) >= balance ? 'bg-gray-700 text-gray-400 cursor-not-allowed' : 'bg-bv-gold-500 text-white hover:bg-bv-gold-600'}`}>Add EMI</button>
            </div>
          </div>
        </div>
      )}

      {(store.payments || []).length > 0 && <div className="space-y-2">
        {(store.payments || []).map((p, i) => (
          <div key={i} className="flex items-center justify-between bg-green-900/30 border border-green-200 rounded-lg px-4 py-2 text-sm">
            <div className="flex items-center gap-2"><CheckCircle className="w-4 h-4 text-green-500" /><span className="font-medium">{p.method}</span>{p.reference && <span className="text-gray-500">({p.reference})</span>}</div>
            <div className="flex items-center gap-2"><span className="font-semibold">{'\u20B9'}{Math.round(p.amount * 100 / 100).toLocaleString('en-IN')}</span><button onClick={() => store.removePayment(i)} className="text-gray-400 hover:text-red-500"><X className="w-4 h-4" /></button></div>
          </div>
        ))}
      </div>}

      {/* Cash change calculator — only if cash payment was added */}
      {(store.payments || []).some(p => p.method === 'CASH') && balance <= 0 && (
        <CashChangeCalculator grandTotal={total} totalPaid={paid} />
      )}

      {balance <= 0 && <div className="bg-green-900/30 border border-green-200 rounded-lg p-4 text-center text-green-700 font-semibold">Payment complete — click "Complete Order" to finalize</div>}
    </div>
  );
}

export default StepPayment;
