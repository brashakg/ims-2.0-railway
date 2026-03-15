// ============================================================================
// IMS 2.0 - POS Layout (Complete Rewrite)
// ============================================================================
// 6-Step Wizard: Customer → Prescription → Products → Review → Payment → Receipt
// Uses posStore (Zustand) for all state with localStorage persistence
// Wires all 12 previously orphaned components + adds 6 missing features
// Theme: Better Vision gold/white (matches app design system)

import { useState, useEffect, useMemo, useRef, startTransition } from 'react';
import {
  ShoppingCart, User, Eye, Package, CreditCard, CheckCircle,
  ChevronRight, ChevronLeft, Search, Plus, X,
  Pause, Play, Printer, RotateCcw, IndianRupee, AlertTriangle,
  Glasses, Watch, Phone, FileText, Zap, Sparkles,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { usePOSStore } from '../../stores/posStore';
import type { SaleType, POSStep, CartLineItem } from '../../stores/posStore';
import { useProducts, useCustomerSearch } from '../../hooks/usePOSQueries';
import { customerApi, orderApi, prescriptionApi } from '../../services/api';
import type { Prescription } from '../../types';

// Backwards-compatible type exports (used by BillingEngine, CartPanel)
export interface CartItem {
  product_id: string;
  name: string;
  sku: string;
  brand?: string;
  unit_price: number;
  quantity: number;
  image_url?: string;
  category: string;
  stock?: number;
  is_optical?: boolean;
  discount_percent?: number;
}

export interface Customer {
  id: string;
  name: string;
  phone: string;
  email?: string;
  address?: string;
  loyalty_points?: number;
}

// Import orphaned components
import { PrescriptionForm } from './PrescriptionForm';
import { PrescriptionPanel } from './PrescriptionPanel';
import { PrescriptionSelectModal } from './PrescriptionSelectModal';
import { LensDetailsModal } from './LensDetailsModal';
import { LensSuggestionPanel } from './LensSuggestionPanel';
import { DiscountModal } from './DiscountModal';
import { GSTInvoice } from './GSTInvoice';
import { ReceiptPreview } from './ReceiptPreview';
import { BarcodeScanner } from './BarcodeScanner';
import { getGSTRateByCategory } from '../../constants/gst';
import type { PrescriptionInput } from '../../utils/lensAutoSuggest';

// ============================================================================
// Constants
// ============================================================================

const STEPS: { id: POSStep; label: string; icon: typeof User }[] = [
  { id: 'customer', label: 'Customer', icon: User },
  { id: 'prescription', label: 'Prescription', icon: Eye },
  { id: 'products', label: 'Products', icon: Package },
  { id: 'review', label: 'Review', icon: ShoppingCart },
  { id: 'payment', label: 'Payment', icon: CreditCard },
  { id: 'complete', label: 'Complete', icon: CheckCircle },
];

const QUICK_STEPS: POSStep[] = ['customer', 'products', 'payment', 'complete'];

function mapCategory(cat: string): string {
  const map: Record<string, string> = {
    FRAMES: 'FRAME', FRAME: 'FRAME', SUNGLASSES: 'SUNGLASS', SUNGLASS: 'SUNGLASS',
    RX_LENSES: 'LENS', OPTICAL_LENS: 'LENS', CONTACT_LENSES: 'CONTACT_LENS',
    ACCESSORIES: 'ACCESSORY', WRIST_WATCHES: 'WATCH', SMARTWATCHES: 'SMARTWATCH', SERVICES: 'SERVICE',
  };
  return map[cat] || cat;
}

// ============================================================================
// Main POS Layout
// ============================================================================

export function POSLayout() {
  const { user } = useAuth();
  const store = usePOSStore();

  const [showPrescriptionModal, setShowPrescriptionModal] = useState(false);
  const [showNewPrescription, setShowNewPrescription] = useState(false);
  const [showLensModal, setShowLensModal] = useState(false);
  const [discountItem, setDiscountItem] = useState<CartLineItem | null>(null);
  const [showReceipt, setShowReceipt] = useState(false);
  const [holdConfirm, setHoldConfirm] = useState(false);
  const [showRecallPanel, setShowRecallPanel] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Held bills from localStorage
  const getHeldBills = (): Array<{ id: string; customer: string; items: number; total: number; heldAt: string; state: any }> => {
    try { return JSON.parse(localStorage.getItem('ims-held-bills') || '[]'); } catch { return []; }
  };

  const holdCurrentBill = () => {
    const bills = getHeldBills();
    bills.push({
      id: `hold-${Date.now()}`,
      customer: store.customer?.name || 'Walk-in',
      items: (store.cart || []).length,
      total: store.getGrandTotal(),
      heldAt: new Date().toISOString(),
      state: {
        sale_type: store.sale_type,
        customer: store.customer,
        patient: store.patient,
        prescription: store.prescription,
        cart: store.cart,
        cart_note: store.cart_note,
        payments: store.payments,
        is_advance_payment: store.is_advance_payment,
      },
    });
    localStorage.setItem('ims-held-bills', JSON.stringify(bills));
    store.resetTransaction();
    setHoldConfirm(false);
  };

  const recallBill = (billId: string) => {
    const bills = getHeldBills();
    const bill = bills.find(b => b.id === billId);
    if (!bill) return;
    // Restore state
    const s = bill.state;
    if (s.customer) store.setCustomer(s.customer);
    if (s.patient) store.setPatient(s.patient);
    if (s.prescription) store.setPrescription(s.prescription);
    if (s.sale_type) store.setSaleType(s.sale_type);
    if (s.cart_note) store.setCartNote(s.cart_note);
    if (s.is_advance_payment) store.setAdvancePayment(s.is_advance_payment);
    // Restore cart items
    for (const item of (s.cart || [])) {
      store.addToCart(item);
    }
    // Restore payments
    for (const p of (s.payments || [])) {
      store.addPayment(p);
    }
    // Remove from held bills
    localStorage.setItem('ims-held-bills', JSON.stringify(bills.filter(b => b.id !== billId)));
    setShowRecallPanel(false);
    // Navigate to products or review
    if ((s.cart || []).length > 0) store.setStep('review');
  };

  const deleteHeldBill = (billId: string) => {
    const bills = getHeldBills().filter(b => b.id !== billId);
    localStorage.setItem('ims-held-bills', JSON.stringify(bills));
  };

  // Safe reset: clear zustand + all local state
  const handleFullReset = () => {
    setShowPrescriptionModal(false);
    setShowNewPrescription(false);
    setShowLensModal(false);
    setDiscountItem(null);
    setShowReceipt(false);
    setHoldConfirm(false);
    setShowRecallPanel(false);
    setErrorMsg(null);
    store.resetTransaction();
  };

  useEffect(() => {
    if (user && !store.salesperson_id) {
      store.setStoreId(user.activeStoreId || user.storeIds?.[0] || '');
      store.setSalesperson(user.id, user.name);
    }
  }, [user]);

  const activeSteps = store.sale_type === 'quick_sale' ? QUICK_STEPS : STEPS.map(s => s.id);
  const currentStepIndex = activeSteps.indexOf(store.current_step);
  const visibleSteps = STEPS.filter(s => activeSteps.includes(s.id));

  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (e.key === 'F2') { e.preventDefault(); store.setStep('products'); }
      if (e.key === 'F9' && (store.cart || []).length > 0) { e.preventDefault(); store.setStep('payment'); }
      if (e.key === 'Escape' && store.current_step !== 'customer') { e.preventDefault(); store.prevStep(); }
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [(store.cart || []).length, store.current_step]);

  const canProceed = useMemo(() => {
    switch (store.current_step) {
      case 'customer': return !!store.customer;
      case 'prescription': return !!store.prescription;
      case 'products': return (store.cart || []).length > 0;
      case 'review': return (store.cart || []).length > 0;
      case 'payment': {
        // Advance payment: at least one payment recorded is enough
        if (store.is_advance_payment) return store.getTotalPaid() > 0;
        // Full payment: balance must be zero
        return store.getBalance() <= 0.01;
      }
      default: return true;
    }
  }, [store.current_step, store.customer, store.prescription, store.cart, store.payments, store.is_advance_payment]);

  async function handleCreateOrder() {
    if (store.is_processing) return; // Double-click guard
    
    // Rx order validation: must have at least one lens if prescription order
    if (store.sale_type === 'prescription_order') {
      const hasLens = (store.cart || []).some(i => 
        i.category === 'RX_LENSES' || i.lens_details || i.is_optical
      );
      if (!hasLens) {
        setErrorMsg('Prescription order requires at least one lens item. Add lenses or switch to Quick Sale.');
        return;
      }
    }

    // Payment validation
    if (store.getBalance() > 0.01 && !store.is_advance_payment) {
      setErrorMsg('Payment incomplete. Add payments or enable "Advance payment only".');
      return;
    }

    setErrorMsg(null);
    store.setProcessing(true);
    try {
      const result = await orderApi.createOrder({
        customer_id: store.customer?.id,
        store_id: store.store_id,
        order_type: store.sale_type,
        salesperson_id: store.salesperson_id,
        items: (store.cart || []).map(item => ({
          item_type: mapCategory(item.category),
          product_id: item.product_id,
          product_name: item.name,
          sku: item.sku,
          brand: item.brand,
          subbrand: item.subbrand,
          category: item.category,
          quantity: item.quantity,
          unit_price: item.unit_price,
          discount_percent: item.discount_percent,
          prescription_id: item.linked_prescription_id,
          lens_details: item.lens_details,
        })),
        notes: store.cart_note || undefined,
      } as any);
      if (result?.order_id) {
        for (const p of (store.payments || [])) {
          try {
            await orderApi.addPayment(result.order_id, { method: p.method, amount: p.amount, reference: p.reference } as any);
          } catch {
            console.error('Payment recording failed for', p.method, p.amount);
            // Don't block order — payment can be recorded later
          }
        }
        store.setOrderResult(result.order_id, result.order_number);
        store.setStep('complete');
      } else {
        setErrorMsg('Order created but no ID returned. Check order list.');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMsg('Failed to create order: ' + (msg || 'Network error'));
      console.error('Order creation error:', err);
    } finally {
      store.setProcessing(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* HEADER */}
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-bv-gold-500 rounded-lg flex items-center justify-center">
            <ShoppingCart className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Point of Sale</h1>
            <p className="text-xs text-gray-500">{user?.name} · {store.store_id || 'No store'}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setHoldConfirm(true)} disabled={(store.cart || []).length === 0}
            className="flex items-center gap-1.5 px-3 py-2 text-sm bg-amber-50 text-amber-700 border border-amber-200 rounded-lg hover:bg-amber-100 disabled:opacity-40">
            <Pause className="w-4 h-4" /> Hold
          </button>
          <button className="flex items-center gap-1.5 px-3 py-2 text-sm bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 relative" onClick={() => setShowRecallPanel(true)}>
            <Play className="w-4 h-4" /> Recall
            {getHeldBills().length > 0 && <span className="absolute -top-1 -right-1 w-4 h-4 bg-amber-500 text-white text-[10px] rounded-full flex items-center justify-center">{getHeldBills().length}</span>}
          </button>
          <button onClick={() => { if (window.confirm('Start new transaction?')) handleFullReset(); }}
            className="flex items-center gap-1.5 px-3 py-2 text-sm bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200">
            <RotateCcw className="w-4 h-4" /> New
          </button>
          <div className="hidden laptop:flex items-center gap-2 text-xs text-gray-400 ml-2 border-l border-gray-200 pl-3">
            <kbd className="px-1.5 py-0.5 bg-gray-100 border rounded text-[10px]">F2</kbd> Search
            <kbd className="px-1.5 py-0.5 bg-gray-100 border rounded text-[10px]">F9</kbd> Pay
            <kbd className="px-1.5 py-0.5 bg-gray-100 border rounded text-[10px]">ESC</kbd> Back
          </div>
        </div>
      </header>

      {/* STEP INDICATOR */}
      <div className="bg-white border-b border-gray-200 px-4 py-2">
        <div className="flex items-center gap-1 overflow-x-auto">
          {visibleSteps.map((step, idx) => {
            const stepIdx = activeSteps.indexOf(step.id);
            const isActive = step.id === store.current_step;
            const isComplete = stepIdx < currentStepIndex;
            const Icon = step.icon;
            return (
              <div key={step.id} className="flex items-center">
                {idx > 0 && <ChevronRight className="w-4 h-4 text-gray-300 mx-1 flex-shrink-0" />}
                <button onClick={() => { if (isComplete) store.setStep(step.id); }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
                    isActive ? 'bg-bv-gold-500 text-white' : isComplete ? 'bg-bv-gold-50 text-bv-gold-700 cursor-pointer hover:bg-bv-gold-100' : 'text-gray-400'
                  }`}>
                  <Icon className="w-4 h-4" /> {step.label} {isComplete && <CheckCircle className="w-3.5 h-3.5" />}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* MAIN CONTENT */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 overflow-y-auto p-4 tablet:p-6">
          {store.current_step === 'customer' && <StepCustomer />}
          {store.current_step === 'prescription' && <StepPrescription onShowModal={() => setShowPrescriptionModal(true)} onShowNew={() => setShowNewPrescription(true)} />}
          {store.current_step === 'products' && <StepProducts onOpenLensModal={() => setShowLensModal(true)} />}
          {store.current_step === 'review' && <StepReview onOpenDiscount={(item) => setDiscountItem(item)} />}
          {store.current_step === 'payment' && <StepPayment />}
          {store.current_step === 'complete' && <StepComplete onPrint={() => setShowReceipt(true)} onReset={handleFullReset} />}
        </div>

        {(['products', 'review', 'prescription'] as POSStep[]).includes(store.current_step) && (store.cart || []).length > 0 && (
          <div className="hidden tablet:flex w-80 laptop:w-96 border-l border-gray-200 bg-white flex-col">
            <CartSidebar />
          </div>
        )}
      </div>

      {/* FOOTER NAV */}
      {errorMsg && (
        <div className="bg-red-50 border-t border-red-200 px-4 py-2.5 flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} className="text-red-400 hover:text-red-600 ml-2"><X className="w-4 h-4" /></button>
        </div>
      )}
      <footer className="bg-white border-t border-gray-200 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          {currentStepIndex > 0 && store.current_step !== 'complete' && (
            <button onClick={() => store.prevStep()} className="flex items-center gap-1.5 px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">
              <ChevronLeft className="w-4 h-4" /> Back
            </button>
          )}
          {(store.cart || []).length > 0 && (
            <div className="text-sm text-gray-500">
              <span className="font-semibold text-gray-900">{(store.cart || []).length}</span> items · <span className="font-semibold text-gray-900 ml-1">₹{store.getGrandTotal().toLocaleString('en-IN')}</span>
            </div>
          )}
        </div>
        {store.current_step !== 'complete' && (
          <button onClick={() => { setErrorMsg(null); store.current_step === 'payment' ? handleCreateOrder() : store.nextStep(); }}
            disabled={!canProceed || store.is_processing}
            className={`flex items-center gap-1.5 px-6 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
              !canProceed || store.is_processing ? 'bg-gray-200 text-gray-400 cursor-not-allowed' : 'bg-bv-gold-500 text-white hover:bg-bv-gold-600'
            }`}>
            {store.is_processing ? (
              <><span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Processing...</>
            ) : (
              <>{store.current_step === 'payment' ? 'Complete Order' : 'Continue'} <ChevronRight className="w-4 h-4" /></>
            )}
          </button>
        )}
      </footer>

      {/* MODALS */}
      {showPrescriptionModal && store.customer && (
        <PrescriptionSelectModal patient={store.patient} customerId={store.customer.id} currentPrescriptionId={store.prescription?.id}
          onSelect={(rx) => { store.setPrescription(rx); setShowPrescriptionModal(false); }}
          onCreateNew={() => { setShowPrescriptionModal(false); setShowNewPrescription(true); }}
          onClose={() => setShowPrescriptionModal(false)} />
      )}
      {showNewPrescription && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">New Prescription</h3>
              <button onClick={() => { setShowNewPrescription(false); setErrorMsg(null); }} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
            </div>
            {errorMsg && (
              <div className="mx-4 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div><p className="font-medium">Failed to save prescription</p><p className="text-xs mt-0.5">{errorMsg}</p></div>
                <button onClick={() => setErrorMsg(null)} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
              </div>
            )}
            <div className="p-4">
              <PrescriptionForm
                onSubmit={async (rxData) => {
                  setErrorMsg(null);
                  try {
                    // Determine source and optometrist
                    const isOptometrist = user?.roles?.includes('OPTOMETRIST');
                    const source = isOptometrist ? 'TESTED_AT_STORE' : 'FROM_DOCTOR';
                    
                    const result = await prescriptionApi.createPrescription({
                      patient_id: store.patient?.id || store.customer?.id,
                      customer_id: store.customer?.id,
                      source,
                      optometrist_id: isOptometrist ? user?.id : (user?.id || 'admin-override'),
                      validity_months: 12,
                      right_eye: { sph: String(rxData.sph_od || 0), cyl: String(rxData.cyl_od || 0), axis: rxData.axis_od || 180, add: String(rxData.add_od || 0), pd: String(rxData.pd_od || '') },
                      left_eye: { sph: String(rxData.sph_os || 0), cyl: String(rxData.cyl_os || 0), axis: rxData.axis_os || 180, add: String(rxData.add_os || 0), pd: String(rxData.pd_os || '') },
                      remarks: rxData.doctor_name ? `Dr. ${rxData.doctor_name}` : undefined,
                    } as any);

                    if (result?.prescription_id) {
                      store.setPrescription({
                        id: result.prescription_id,
                        patientId: store.patient?.id || '',
                        customerId: store.customer?.id || '',
                        storeId: store.store_id,
                        testDate: new Date().toISOString(),
                        rightEye: { sphere: rxData.sph_od || 0, cylinder: rxData.cyl_od || null, axis: rxData.axis_od || null, add: rxData.add_od || null, pd: rxData.pd_od || 0 },
                        leftEye: { sphere: rxData.sph_os || 0, cylinder: rxData.cyl_os || null, axis: rxData.axis_os || null, add: rxData.add_os || null, pd: rxData.pd_os || 0 },
                        status: 'COMPLETED',
                        createdAt: new Date().toISOString(),
                        updatedAt: new Date().toISOString(),
                      } as Prescription);
                      setErrorMsg(null);
                      setShowNewPrescription(false);
                    } else {
                      setErrorMsg('Prescription saved but no ID returned. Try selecting from existing prescriptions.');
                    }
                  } catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setErrorMsg(msg || 'Network error — check your connection and try again');
                    console.error('Prescription save error:', err);
                  }
                }}
                onCancel={() => { setShowNewPrescription(false); setErrorMsg(null); }}
              />
            </div>
          </div>
        </div>
      )}
      {showLensModal && (
        <LensDetailsModal onClose={() => setShowLensModal(false)}
          onSave={(details) => {
            store.addToCart({ product_id: `lens-${Date.now()}`, name: `${details.lensCategory} - ${details.indexLabel}`, sku: `RX-CUSTOM-${Date.now()}`,
              category: 'RX_LENSES', unit_price: details.totalPrice || 0, mrp: details.totalPrice || 0, quantity: 2, is_optical: true,
              linked_prescription_id: store.prescription?.id, lens_details: { type: details.lensCategory, material: details.indexLabel, index: details.indexId, coatings: [details.coatingLabel].filter(Boolean) } });
            setShowLensModal(false);
          }} />
      )}
      {discountItem && (
        <DiscountModal item={{ product_id: discountItem.product_id, name: discountItem.name, sku: discountItem.sku, unitPrice: discountItem.unit_price, quantity: discountItem.quantity, category: discountItem.category, brand: discountItem.brand, discountPercent: discountItem.discount_percent } as any}
          maxDiscountPercent={user?.discountCap || 10}
          onApply={(pct) => { store.applyDiscount(discountItem.id, pct); setDiscountItem(null); }}
          onClose={() => setDiscountItem(null)} />
      )}
      {showReceipt && (
        <ReceiptPreview billData={{ bill_number: store.order_number || 'N/A', subtotal: store.getSubtotal(), gst_amount: store.getGrandTotal() - store.getSubtotal(), discount_amount: store.getTotalDiscount(), total_amount: store.getGrandTotal(), payment_method: (store.payments || []).map(p => p.method).join(' + ') || 'N/A' }}
          selectedCustomer={store.customer || { name: 'Walk-in', phone: '' }} cartItems={(store.cart || []) as any} onClose={() => setShowReceipt(false)} />
      )}
      {holdConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 max-w-sm">
            <h3 className="font-semibold text-gray-900 mb-2">Hold this bill?</h3>
            <p className="text-sm text-gray-500 mb-1">{store.customer?.name || 'Walk-in'} · {(store.cart || []).length} items · ₹{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}</p>
            <p className="text-xs text-gray-400 mb-4">Cart will be saved and can be recalled later.</p>
            <div className="flex gap-2">
              <button onClick={() => setHoldConfirm(false)} className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm">Cancel</button>
              <button onClick={holdCurrentBill} className="flex-1 px-4 py-2 bg-amber-500 text-white rounded-lg text-sm font-semibold">Hold Bill</button>
            </div>
          </div>
        </div>
      )}
      {showRecallPanel && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md max-h-[70vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">Held Bills ({getHeldBills().length})</h3>
              <button onClick={() => setShowRecallPanel(false)} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-4 space-y-2">
              {getHeldBills().length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-8">No held bills</p>
              ) : getHeldBills().map(bill => (
                <div key={bill.id} className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm text-gray-900">{bill.customer}</p>
                    <p className="text-xs text-gray-500">{bill.items} items · ₹{Math.round(bill.total).toLocaleString('en-IN')}</p>
                    <p className="text-[10px] text-gray-400">{new Date(bill.heldAt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</p>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => { deleteHeldBill(bill.id); setShowRecallPanel(false); setTimeout(() => setShowRecallPanel(true), 50); }}
                      className="text-xs text-red-500 hover:text-red-700 px-2 py-1">Delete</button>
                    <button onClick={() => recallBill(bill.id)}
                      className="text-xs bg-bv-gold-500 text-white px-3 py-1 rounded font-semibold hover:bg-bv-gold-600">Recall</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// STEP 1: Customer + Sale Type
// ============================================================================
function StepCustomer() {
  const store = usePOSStore();
  const [searchInput, setSearchInput] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [custError, setCustError] = useState<string | null>(null);
  const [newCust, setNewCust] = useState({ name: '', phone: '', email: '' });
  const { data: searchResults = [], isLoading } = useCustomerSearch(debouncedQuery);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce search — 300ms prevents keystroke-level API spam (INP fix)
  const handleSearchInput = (val: string) => {
    setSearchInput(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      startTransition(() => setDebouncedQuery(val));
    }, 300);
  };

  const isWalkin = store.customer?.id?.toString().startsWith('walkin-') || store.customer?.name === 'Walk-in Customer';

  // Force quick_sale if walk-in is selected
  useEffect(() => {
    if (isWalkin && store.sale_type === 'prescription_order') {
      store.setSaleType('quick_sale');
    }
  }, [isWalkin]);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Sale Type</label>
        <div className="grid grid-cols-2 gap-3">
          {([
            { id: 'quick_sale' as SaleType, label: 'Quick Sale', desc: 'Frames, sunglasses, accessories — immediate delivery', icon: Zap, blocked: false },
            { id: 'prescription_order' as SaleType, label: 'Prescription Order', desc: isWalkin ? 'Register customer first for Rx orders' : 'Frame + lens with Rx — workshop job created', icon: Eye, blocked: isWalkin },
          ]).map(opt => (
            <button key={opt.id} onClick={() => { if (!opt.blocked) store.setSaleType(opt.id); }}
              title={opt.blocked ? 'Select a registered customer for prescription orders' : ''}
              className={`flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all ${
                opt.blocked ? 'border-gray-100 bg-gray-50 opacity-50 cursor-not-allowed' :
                store.sale_type === opt.id ? 'border-bv-gold-500 bg-bv-gold-50' : 'border-gray-200 hover:border-gray-300'}`}>
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${
                opt.blocked ? 'bg-gray-200 text-gray-400' :
                store.sale_type === opt.id ? 'bg-bv-gold-500 text-white' : 'bg-gray-100 text-gray-500'}`}>
                <opt.icon className="w-5 h-5" />
              </div>
              <div>
                <p className={`font-semibold ${opt.blocked ? 'text-gray-400' : 'text-gray-900'}`}>{opt.label}</p>
                <p className={`text-xs mt-0.5 ${opt.blocked ? 'text-red-400' : 'text-gray-500'}`}>{opt.desc}</p>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Customer</label>
        {store.customer ? (
          <div className={`${isWalkin ? 'bg-gray-50 border-gray-200' : 'bg-bv-gold-50 border-bv-gold-200'} border rounded-xl p-4 flex items-center justify-between`}>
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full ${isWalkin ? 'bg-gray-400' : 'bg-bv-gold-500'} text-white flex items-center justify-center font-semibold`}>{store.customer.name?.charAt(0) || 'W'}</div>
              <div>
                <p className="font-semibold text-gray-900">{store.customer.name}</p>
                <p className="text-sm text-gray-500">{store.customer.phone || 'No phone'}</p>
                {isWalkin && <p className="text-xs text-amber-600 mt-0.5">Walk-in — Quick Sale only</p>}
                {store.patient && <p className="text-xs text-bv-gold-600 mt-0.5">Patient: {store.patient.name}</p>}
              </div>
            </div>
            <button onClick={() => store.setCustomer(null)} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1 border border-gray-200 rounded-lg">Change</button>
          </div>
        ) : (
          <>
            <div className="relative">
              <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
              <input type="text" value={searchInput} onChange={(e) => handleSearchInput(e.target.value)} placeholder="Search by phone number or name..." autoFocus
                className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-bv-gold-500 focus:border-bv-gold-500" />
            </div>
            {searchInput.length >= 2 && (
              <div className="mt-2 border border-gray-200 rounded-lg overflow-hidden">
                {isLoading ? <div className="p-4 text-center text-sm text-gray-500">Searching...</div>
                : (searchResults as any[]).length > 0 ? (searchResults as any[]).slice(0, 8).map((cust: any) => (
                  <button key={cust.customer_id || cust._id || cust.id} onClick={() => {
                    startTransition(() => {
                      store.setCustomer({ ...cust, id: cust.customer_id || cust._id || cust.id } as any);
                    });
                    setSearchInput(''); setDebouncedQuery('');
                  }}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 border-b border-gray-100 last:border-0 text-left">
                    <div className="w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center text-sm font-medium text-gray-600">{cust.name?.charAt(0)}</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">{cust.name}</p>
                      <p className="text-xs text-gray-500">{cust.phone} {cust.city && `· ${cust.city}`}</p>
                    </div>
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                  </button>
                )) : <div className="p-4 text-center text-sm text-gray-500">No customers found</div>}
              </div>
            )}
            <div className="mt-3 flex gap-4">
              <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 text-sm text-bv-gold-600 hover:text-bv-gold-700 font-medium"><Plus className="w-4 h-4" /> Create new customer</button>
              <button onClick={async () => {
                try {
                  const r = await customerApi.createCustomer({ name: 'Walk-in Customer', mobile: '0000000000', customer_type: 'B2C' } as any);
                  store.setCustomer({ id: r?.customer_id || r?.id || `walkin-${Date.now()}`, name: 'Walk-in Customer', phone: '0000000000', email: '', customerType: 'B2C' } as any);
                } catch {
                  store.setCustomer({ id: `walkin-${Date.now()}`, name: 'Walk-in Customer', phone: '', email: '', customerType: 'B2C' } as any);
                }
                store.setSaleType('quick_sale');
              }}
                className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700"><User className="w-4 h-4" /> Walk-in (Quick Sale only)</button>
            </div>
          </>
        )}
      </div>

      {showCreate && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          <h4 className="font-semibold text-gray-900 text-sm">New Customer</h4>
          <input placeholder="Full name *" value={newCust.name} onChange={(e) => { setNewCust(p => ({ ...p, name: e.target.value })); setCustError(null); }} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <input placeholder="Phone number (10 digits) *" value={newCust.phone} onChange={(e) => { setNewCust(p => ({ ...p, phone: e.target.value.replace(/\D/g, '').slice(0, 10) })); setCustError(null); }} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <input placeholder="Email (optional)" type="email" value={newCust.email} onChange={(e) => { setNewCust(p => ({ ...p, email: e.target.value })); setCustError(null); }} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          {custError && <p className="text-xs text-red-600 bg-red-50 px-2 py-1 rounded">{custError}</p>}
          <div className="flex gap-2">
            <button onClick={() => { setShowCreate(false); setCustError(null); }} className="px-4 py-2 text-sm border border-gray-300 rounded-lg">Cancel</button>
            <button onClick={async () => {
              if (!newCust.name.trim()) { setCustError('Name is required'); return; }
              if (!newCust.phone || newCust.phone.length !== 10) { setCustError('Valid 10-digit phone required'); return; }
              if (newCust.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newCust.email)) { setCustError('Invalid email format'); return; }
              setCustError(null);
              try {
                const r = await customerApi.createCustomer({ name: newCust.name.trim(), mobile: newCust.phone, email: newCust.email || undefined, customer_type: 'B2C' } as any);
                store.setCustomer({ id: r?.customer_id || r?.id || `new-${Date.now()}`, name: newCust.name.trim(), phone: newCust.phone, email: newCust.email, customerType: 'B2C' } as any);
                setShowCreate(false);
              } catch (err) {
                const msg = err instanceof Error ? err.message : 'Could not create customer';
                setCustError(msg.includes('already exists') ? 'Phone number already registered. Search for existing customer.' : msg);
              }
            }} className="px-4 py-2 text-sm bg-bv-gold-500 text-white rounded-lg font-semibold">Create & Select</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// STEP 2: Prescription
// ============================================================================
function StepPrescription({ onShowModal, onShowNew }: { onShowModal: () => void; onShowNew: () => void }) {
  const store = usePOSStore();
  if (store.prescription) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">Selected Prescription</h3>
          <button onClick={onShowModal} className="text-sm text-bv-gold-600 hover:text-bv-gold-700 font-medium">Change</button>
        </div>
        <PrescriptionPanel prescription={store.prescription} patientName={store.patient?.name || store.customer?.name} readOnly />
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center gap-2 text-sm text-green-700">
          <CheckCircle className="w-4 h-4" /> Prescription attached — you can now select lenses
        </div>
      </div>
    );
  }
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div><h3 className="font-semibold text-gray-900 mb-1">Prescription Required</h3><p className="text-sm text-gray-500">Select existing or enter a new prescription.</p></div>
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <button onClick={onShowModal} className="flex items-start gap-3 p-4 rounded-xl border-2 border-gray-200 hover:border-bv-gold-300 text-left">
          <div className="w-10 h-10 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center"><FileText className="w-5 h-5" /></div>
          <div><p className="font-semibold text-gray-900">Existing Prescription</p><p className="text-xs text-gray-500 mt-0.5">From patient history</p></div>
        </button>
        <button onClick={onShowNew} className="flex items-start gap-3 p-4 rounded-xl border-2 border-gray-200 hover:border-bv-gold-300 text-left">
          <div className="w-10 h-10 rounded-lg bg-green-50 text-green-600 flex items-center justify-center"><Plus className="w-5 h-5" /></div>
          <div><p className="font-semibold text-gray-900">New Prescription</p><p className="text-xs text-gray-500 mt-0.5">Enter a new Rx now</p></div>
        </button>
      </div>
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-center gap-2 text-sm text-amber-700">
        <AlertTriangle className="w-4 h-4 flex-shrink-0" /> Prescription must be attached before lens selection.
      </div>
    </div>
  );
}

// ============================================================================
// STEP 3: Products
// ============================================================================
function StepProducts({ onOpenLensModal }: { onOpenLensModal: () => void }) {
  const store = usePOSStore();
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [blockMsg, setBlockMsg] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { data: products = [], isLoading } = useProducts({ search: debouncedSearch || undefined, category: categoryFilter || undefined });
  const categories = ['FRAMES', 'SUNGLASSES', 'RX_LENSES', 'CONTACT_LENSES', 'WRIST_WATCHES', 'SMARTWATCHES', 'ACCESSORIES'];

  // Debounce product search — 300ms (INP fix)
  const handleProductSearch = (val: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      startTransition(() => setDebouncedSearch(val));
    }, 300);
  };

  // Build prescription input for lens suggestions (optional panel)
  const rxInput: PrescriptionInput | null = store.prescription ? {
    rightSphere: store.prescription.rightEye?.sphere ?? null,
    rightCylinder: store.prescription.rightEye?.cylinder ?? null,
    rightAxis: store.prescription.rightEye?.axis ?? null,
    rightAdd: store.prescription.rightEye?.add ?? null,
    leftSphere: store.prescription.leftEye?.sphere ?? null,
    leftCylinder: store.prescription.leftEye?.cylinder ?? null,
    leftAxis: store.prescription.leftEye?.axis ?? null,
    leftAdd: store.prescription.leftEye?.add ?? null,
  } : null;

  const handleAddProduct = (product: any) => {
    const mrp = product.mrp || 0;
    const offerPrice = product.offer_price || product.offerPrice || mrp;
    if (offerPrice > mrp && mrp > 0) {
      setBlockMsg(`BLOCKED: ${product.name} — Offer Price (₹${offerPrice.toLocaleString('en-IN')}) exceeds MRP (₹${mrp.toLocaleString('en-IN')}). Contact HQ to fix pricing.`);
      setTimeout(() => setBlockMsg(null), 6000);
      return;
    }
    setBlockMsg(null);
    startTransition(() => {
      store.addToCart({ product_id: product.product_id || product._id || product.id, name: product.name, sku: product.sku, barcode: product.barcode, brand: product.brand, subbrand: product.subbrand || product.sub_brand, category: product.category,
        unit_price: offerPrice || mrp, mrp, offer_price: offerPrice !== mrp ? offerPrice : undefined, quantity: 1,
        is_optical: ['FRAMES', 'RX_LENSES', 'CONTACT_LENSES', 'COLOUR_CONTACTS'].includes(product.category), image_url: product.image_url });
    });
  };

  return (
    <div className="space-y-4">
      {blockMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{blockMsg}</span>
          <button onClick={() => setBlockMsg(null)} className="text-red-400 hover:text-red-600"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}
      <div className="flex gap-3">
        <div className="flex-1">
          <BarcodeScanner onScan={(b) => setDebouncedSearch(b)} onManualSearch={(q) => handleProductSearch(q)} placeholder="Scan barcode or search products..." autoFocus />
        </div>
        {store.sale_type === 'prescription_order' && store.prescription && (
          <button onClick={onOpenLensModal} className="flex items-center gap-2 px-4 py-2 bg-purple-50 text-purple-700 border border-purple-200 rounded-lg hover:bg-purple-100 whitespace-nowrap text-sm font-medium">
            <Eye className="w-4 h-4" /> Add Lens (Manual)
          </button>
        )}
      </div>

      {/* Optional Lens Suggestions — only for Rx orders, dismissible */}
      {store.sale_type === 'prescription_order' && rxInput && showSuggestions && (
        <div className="relative">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-sm font-medium text-purple-700">
              <Sparkles className="w-4 h-4" /> Recommended Lenses (based on Rx)
              <span className="text-xs text-gray-400 font-normal">— suggestions only, staff can override</span>
            </div>
            <button onClick={() => setShowSuggestions(false)} className="text-xs text-gray-400 hover:text-gray-600 px-2 py-1">Dismiss</button>
          </div>
          <LensSuggestionPanel
            prescriptionInput={rxInput}
            onSelect={(suggestion) => {
              // Add suggested lens to cart — staff can still modify or remove
              store.addToCart({
                product_id: `lens-sug-${Date.now()}`,
                name: `${suggestion.lensType} — ${suggestion.material}`,
                sku: `RX-${suggestion.material.replace(/[^a-zA-Z0-9]/g, '').toUpperCase()}-${Date.now().toString(36)}`,
                category: 'RX_LENSES',
                unit_price: suggestion.priceRange.min,
                mrp: suggestion.priceRange.max,
                quantity: 2,
                is_optical: true,
                linked_prescription_id: store.prescription?.id,
                lens_details: {
                  type: suggestion.lensType,
                  material: suggestion.material,
                  coatings: suggestion.coatings,
                },
              });
            }}
          />
        </div>
      )}

      <div className="flex gap-2 overflow-x-auto pb-1">
        <button onClick={() => setCategoryFilter('')} className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${!categoryFilter ? 'bg-bv-gold-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>All</button>
        {categories.map(cat => (
          <button key={cat} onClick={() => setCategoryFilter(cat === categoryFilter ? '' : cat)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${categoryFilter === cat ? 'bg-bv-gold-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
            {cat.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {store.sale_type === 'prescription_order' && store.prescription && (
        <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 flex items-center gap-3 text-sm">
          <Eye className="w-4 h-4 text-purple-600 flex-shrink-0" />
          <span className="text-purple-700 font-medium">Rx:</span>
          <span className="text-purple-500">OD {store.prescription.rightEye?.sphere}/{store.prescription.rightEye?.cylinder} · OS {store.prescription.leftEye?.sphere}/{store.prescription.leftEye?.cylinder}</span>
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-3">
          {[...Array(8)].map((_, i) => <div key={i} className="bg-white rounded-xl border border-gray-200 p-3 animate-pulse"><div className="h-20 bg-gray-100 rounded-lg mb-2" /><div className="h-4 bg-gray-100 rounded w-3/4 mb-1" /><div className="h-3 bg-gray-100 rounded w-1/2" /></div>)}
        </div>
      ) : (
        <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-3">
          {(products as any[]).map((product: any) => {
            const mrp = product.mrp || 0; const offer = product.offer_price || mrp; const hasDiscount = offer < mrp;
            const inCart = (store.cart || []).some(i => i.product_id === (product.product_id || product._id));
            const stock = product.stock ?? product.quantity ?? product.stock_available ?? null;
            const isLowStock = stock !== null && stock > 0 && stock <= 3;
            const isOutOfStock = stock !== null && stock <= 0;
            return (
              <button key={product.product_id || product._id} onClick={() => handleAddProduct(product)} disabled={inCart || isOutOfStock}
                className={`bg-white rounded-xl border text-left p-3 transition-all hover:shadow-md ${
                  isOutOfStock ? 'border-red-200 bg-red-50/30 opacity-60 cursor-not-allowed' :
                  inCart ? 'border-green-300 bg-green-50 opacity-70' : 'border-gray-200 hover:border-bv-gold-300'}`}>
                <div className="h-16 bg-gray-50 rounded-lg mb-2 flex items-center justify-center relative">
                  {product.image_url ? <img src={product.image_url} alt="" className="h-14 w-auto object-contain" /> :
                  product.category === 'FRAMES' || product.category === 'SUNGLASSES' ? <Glasses className="w-8 h-8 text-gray-300" />
                  : product.category?.includes('WATCH') ? <Watch className="w-8 h-8 text-gray-300" /> : <Package className="w-8 h-8 text-gray-300" />}
                  {stock !== null && (
                    <span className={`absolute top-1 right-1 text-[9px] px-1 py-0.5 rounded font-medium ${
                      isOutOfStock ? 'bg-red-100 text-red-600' : isLowStock ? 'bg-amber-100 text-amber-700' : 'bg-green-50 text-green-600'
                    }`}>{isOutOfStock ? 'Out' : isLowStock ? `${stock} left` : `${stock}`}</span>
                  )}
                </div>
                <p className="text-xs font-semibold text-gray-900 truncate">{product.name}</p>
                <p className="text-[10px] text-gray-500 truncate">{product.brand} · {product.sku}</p>
                <div className="mt-1.5 flex items-baseline gap-1.5">
                  <span className="text-sm font-bold text-gray-900">₹{offer.toLocaleString('en-IN')}</span>
                  {hasDiscount && <span className="text-[10px] text-gray-400 line-through">₹{mrp.toLocaleString('en-IN')}</span>}
                </div>
                {inCart && <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 bg-green-100 text-green-700 rounded font-medium">In cart</span>}
                {isOutOfStock && <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 bg-red-100 text-red-600 rounded font-medium">Out of stock</span>}
              </button>
            );
          })}
        </div>
      )}
      {!isLoading && (products as any[]).length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No products found</p>
          {categoryFilter && debouncedSearch && <p className="text-xs mt-1">Search is filtered to <span className="font-medium">{categoryFilter.replace(/_/g, ' ')}</span>. <button onClick={() => setCategoryFilter('')} className="text-bv-gold-600 hover:underline">Search all categories</button></p>}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// STEP 4: Review
// ============================================================================
function StepReview({ onOpenDiscount }: { onOpenDiscount: (item: CartLineItem) => void }) {
  const store = usePOSStore();
  const subtotal = store.getSubtotal(); const discount = store.getTotalDiscount();

  // Calculate GST per item based on product category (GST 2.0 rates)
  const taxBreakdown = useMemo(() => {
    let totalTax = 0;
    const rates: Record<number, number> = {}; // rate → taxable amount
    for (const item of (store.cart || [])) {
      const rate = getGSTRateByCategory(item.category);
      const itemTaxable = item.line_total;
      const itemTax = Math.round(itemTaxable * (rate / 100) * 100) / 100;
      totalTax += itemTax;
      rates[rate] = (rates[rate] || 0) + itemTaxable;
    }
    return { totalTax: Math.round(totalTax * 100) / 100, rates };
  }, [store.cart]);

  // Use store's getGrandTotal (includes GST) for consistency with Payment step
  const total = store.getGrandTotal();

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <h3 className="font-semibold text-gray-900">Order Review</h3>
      {store.customer && (
        <div className="bg-gray-50 rounded-lg p-3 flex items-center gap-3 text-sm">
          <User className="w-4 h-4 text-gray-400" /><span className="font-medium">{store.customer.name}</span><span className="text-gray-500">{store.customer.phone}</span>
          {store.sale_type === 'prescription_order' && <span className="ml-auto px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs font-medium">Prescription Order</span>}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
            <tr><th className="text-left px-4 py-2">Item</th><th className="text-center px-2 py-2">Qty</th><th className="text-right px-2 py-2">MRP</th><th className="text-right px-2 py-2">Price</th><th className="text-right px-2 py-2">Disc</th><th className="text-center px-2 py-2">GST</th><th className="text-right px-4 py-2">Total</th><th className="w-8"></th></tr>
          </thead>
          <tbody>
            {(store.cart || []).map(item => {
              const gstRate = getGSTRateByCategory(item.category);
              return (
              <tr key={item.id} className="border-t border-gray-100">
                <td className="px-4 py-3">
                  <p className="font-medium text-gray-900">{item.name}</p>
                  <p className="text-xs text-gray-500">{item.brand} · {item.sku}</p>
                  {item.lens_details && <p className="text-xs text-purple-500 mt-0.5">{item.lens_details.type} · {item.lens_details.coatings.join(', ')}</p>}
                </td>
                <td className="text-center px-2">
                  <div className="flex items-center justify-center gap-1">
                    <button onClick={() => store.updateQuantity(item.id, item.quantity - 1)} className="w-6 h-6 rounded bg-gray-100 text-xs hover:bg-gray-200">-</button>
                    <span className="w-6 text-center font-medium">{item.quantity}</span>
                    <button onClick={() => store.updateQuantity(item.id, item.quantity + 1)} className="w-6 h-6 rounded bg-gray-100 text-xs hover:bg-gray-200">+</button>
                  </div>
                </td>
                <td className="text-right px-2 text-gray-500">₹{item.mrp.toLocaleString('en-IN')}</td>
                <td className="text-right px-2">₹{item.unit_price.toLocaleString('en-IN')}</td>
                <td className="text-right px-2">
                  {item.offer_price && item.offer_price < item.mrp ? (
                    <span className="px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-400 cursor-not-allowed" title="MRP > Offer Price: No further discount allowed">N/A</span>
                  ) : (
                    <button onClick={() => onOpenDiscount(item)}
                      className={`px-2 py-0.5 rounded text-xs ${item.discount_percent > 0 ? 'bg-green-50 text-green-700 font-medium' : 'bg-gray-50 text-gray-500 hover:bg-gray-100'}`}>
                      {item.discount_percent > 0 ? `${item.discount_percent}%` : 'Add'}
                    </button>
                  )}
                </td>
                <td className="text-center px-2 text-xs text-gray-500">{gstRate}%</td>
                <td className="text-right px-4 font-semibold">₹{item.line_total.toLocaleString('en-IN')}</td>
                <td><button onClick={() => store.removeFromCart(item.id)} className="p-1 text-gray-400 hover:text-red-500"><X className="w-4 h-4" /></button></td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <textarea value={store.cart_note} onChange={(e) => store.setCartNote(e.target.value)} placeholder="Order notes, fitting instructions..." className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none" />

      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Subtotal</span><span>₹{subtotal.toLocaleString('en-IN')}</span></div>
        {discount > 0 && <div className="flex justify-between text-green-600"><span>Discount</span><span>-₹{discount.toLocaleString('en-IN')}</span></div>}
        {Object.entries(taxBreakdown.rates).map(([rate, taxable]) => {
          const r = Number(rate);
          const halfRate = r / 2;
          const tax = Math.round((taxable as number) * (r / 100) * 100) / 100;
          return (
            <div key={rate} className="space-y-1">
              <div className="flex justify-between text-gray-500"><span>CGST ({halfRate}%)</span><span>₹{(tax / 2).toLocaleString('en-IN')}</span></div>
              <div className="flex justify-between text-gray-500"><span>SGST ({halfRate}%)</span><span>₹{(tax / 2).toLocaleString('en-IN')}</span></div>
            </div>
          );
        })}
        <div className="border-t border-gray-200 pt-2 flex justify-between font-bold text-lg"><span>Grand Total</span><span className="text-bv-gold-600">₹{total.toLocaleString('en-IN')}</span></div>
      </div>

      {store.sale_type === 'prescription_order' && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={store.is_advance_payment} onChange={(e) => store.setAdvancePayment(e.target.checked)} className="rounded border-gray-300" />
            <span className="font-medium text-blue-700">Advance payment only</span><span className="text-blue-500 text-xs">(Balance on delivery)</span>
          </label>
          {store.is_advance_payment && (
            <div className="mt-2 flex items-center gap-2"><label className="text-xs text-blue-600">Delivery date:</label>
              <input type="date" value={store.delivery_date || ''} onChange={(e) => store.setDeliveryDate(e.target.value)} className="px-2 py-1 border border-blue-200 rounded text-sm" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Cash Change Calculator (used in StepPayment)
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
    <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-3">
      <p className="text-sm font-medium text-gray-700">Cash Tendered</p>
      <div className="flex gap-2 items-center">
        <span className="text-gray-400 text-lg">₹</span>
        <input type="number" value={cashTendered} onChange={(e) => setCashTendered(e.target.value)}
          onFocus={(e) => e.target.select()} placeholder={String(Math.round(grandTotal))}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-lg font-semibold text-center" />
      </div>
      <div className="flex gap-2">
        {quickAmounts.map(amt => (
          <button key={amt} onClick={() => setCashTendered(String(amt))}
            className="px-3 py-1 bg-white border border-gray-200 rounded-lg text-xs font-medium hover:bg-gray-100">₹{amt.toLocaleString('en-IN')}</button>
        ))}
      </div>
      {tendered > 0 && (
        <div className={`text-center py-2 rounded-lg font-bold text-lg ${change >= 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
          {change >= 0 ? `Change: ₹${Math.round(change).toLocaleString('en-IN')}` : `Short: ₹${Math.round(Math.abs(change)).toLocaleString('en-IN')}`}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// STEP 5: Payment
// ============================================================================
function StepPayment() {
  const store = usePOSStore();
  const total = store.getGrandTotal(); const paid = store.getTotalPaid(); const balance = total - paid;
  const [payMethod, setPayMethod] = useState<'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER'>('CASH');
  const [payAmount, setPayAmount] = useState(''); const [payRef, setPayRef] = useState('');
  const methods = [
    { id: 'CASH' as const, label: 'Cash', icon: IndianRupee },
    { id: 'UPI' as const, label: 'UPI', icon: Phone },
    { id: 'CARD' as const, label: 'Card', icon: CreditCard },
    { id: 'BANK_TRANSFER' as const, label: 'Bank', icon: FileText },
  ];

  return (
    <div className="max-w-xl mx-auto space-y-4">
      <div className="bg-white border border-gray-200 rounded-xl p-6 text-center">
        <p className="text-sm text-gray-500 mb-1">{store.is_advance_payment ? 'Advance Due' : 'Total Due (incl. GST)'}</p>
        <p className="text-4xl font-bold text-gray-900">₹{Math.round(total).toLocaleString('en-IN')}</p>
        {paid > 0 && <div className="mt-3 flex justify-center gap-6 text-sm">
          <span className="text-green-600">Paid: ₹{Math.round(paid).toLocaleString('en-IN')}</span>
          <span className={balance > 0 ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}>Balance: ₹{Math.round(Math.max(0, balance)).toLocaleString('en-IN')}</span>
        </div>}
      </div>

      <div className="grid grid-cols-4 gap-2">
        {methods.map(m => (
          <button key={m.id} onClick={() => {
            if (m.id === 'CASH') {
              // Cash: add directly, no ref needed
              store.addPayment({ method: m.id, amount: Math.round((balance > 0 ? balance : total) * 100) / 100 });
            } else {
              // Non-cash: populate split payment fields so user must enter ref
              setPayMethod(m.id);
              setPayAmount(String(Math.round((balance > 0 ? balance : total) * 100) / 100));
              setPayRef('');
            }
          }} disabled={balance <= 0}
            className={`flex flex-col items-center gap-1 p-3 rounded-xl border-2 transition-all ${balance <= 0 ? 'opacity-40 border-gray-200' : 'border-gray-200 hover:border-bv-gold-300 hover:bg-bv-gold-50'}`}>
            <m.icon className="w-6 h-6 text-gray-600" /><span className="text-xs font-medium">{m.id === 'CASH' ? 'Full Cash' : `${m.label} →`}</span>
          </button>
        ))}
      </div>

      {balance > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          <p className="text-sm font-medium text-gray-700">Split payment</p>
          <div className="flex gap-2">
            {methods.map(m => <button key={m.id} onClick={() => setPayMethod(m.id)} className={`px-3 py-1.5 rounded-lg text-xs font-medium ${payMethod === m.id ? 'bg-bv-gold-500 text-white' : 'bg-gray-100 text-gray-600'}`}>{m.label}</button>)}
          </div>
          <div className="flex gap-2">
            <input type="number" min="1" max={balance} step="0.01" value={payAmount} 
              onChange={(e) => setPayAmount(e.target.value)}
              onFocus={(e) => e.target.select()} 
              placeholder={`Amount (max ₹${Math.round(balance).toLocaleString('en-IN')})`} className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
            {payMethod !== 'CASH' && <input value={payRef} onChange={(e) => setPayRef(e.target.value)} placeholder={payMethod === 'UPI' ? 'UPI Txn ID *' : payMethod === 'CARD' ? 'Approval code' : 'Reference'} className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm" />}
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
                  ? 'bg-gray-200 text-gray-400 cursor-not-allowed' : 'bg-bv-gold-500 text-white hover:bg-bv-gold-600'
              }`}>Add</button>
          </div>
          {payMethod !== 'CASH' && !payRef.trim() && payAmount && <p className="text-xs text-amber-600">Reference/Txn ID required for {payMethod}</p>}
        </div>
      )}

      {(store.payments || []).length > 0 && <div className="space-y-2">
        {(store.payments || []).map((p, i) => (
          <div key={i} className="flex items-center justify-between bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm">
            <div className="flex items-center gap-2"><CheckCircle className="w-4 h-4 text-green-500" /><span className="font-medium">{p.method}</span>{p.reference && <span className="text-gray-500">({p.reference})</span>}</div>
            <div className="flex items-center gap-2"><span className="font-semibold">₹{Math.round(p.amount * 100 / 100).toLocaleString('en-IN')}</span><button onClick={() => store.removePayment(i)} className="text-gray-400 hover:text-red-500"><X className="w-4 h-4" /></button></div>
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

// ============================================================================
// STEP 6: Complete
// ============================================================================
function StepComplete({ onPrint, onReset }: { onPrint: () => void; onReset: () => void }) {
  const store = usePOSStore();
  const [showGSTInvoice, setShowGSTInvoice] = useState(false);

  // Build Order-shaped object from POS store for GSTInvoice
  const orderForInvoice = useMemo(() => ({
    id: store.order_id || '',
    orderNumber: store.order_number || '',
    storeId: store.store_id,
    customerId: store.customer?.id || '',
    customerName: store.customer?.name || 'Walk-in',
    customerPhone: store.customer?.phone || '',
    patientName: store.patient?.name,
    items: (store.cart || []).map(item => ({
      id: item.id,
      itemType: item.category || 'FRAMES',
      productId: item.product_id,
      productName: item.name,
      sku: item.sku || '',
      quantity: item.quantity,
      unitPrice: item.unit_price,
      discountPercent: item.discount_percent || 0,
      discountAmount: item.discount_amount || 0,
      finalPrice: item.line_total || item.unit_price * item.quantity,
    })),
    payments: (store.payments || []).map((p, i) => ({
      id: `pay-${i}`,
      mode: p.method,
      amount: p.amount,
      reference: p.reference,
      paidAt: new Date().toISOString(),
    })),
    subtotal: store.getSubtotal(),
    totalDiscount: store.getTotalDiscount(),
    taxAmount: store.getGrandTotal() - store.getSubtotal(),
    grandTotal: store.getGrandTotal(),
    amountPaid: store.getTotalPaid(),
    balanceDue: store.getBalance(),
    orderStatus: 'CONFIRMED',
    createdAt: new Date().toISOString(),
  }), [store.order_id]);

  const storeForInvoice = useMemo(() => ({
    id: store.store_id,
    storeCode: store.store_id,
    storeName: store.store_id?.includes('BOK') ? 'Better Vision Opticals' : store.store_id?.includes('DHB') ? 'Better Vision Opticals' : store.store_id?.includes('WIZ') ? 'WizOpt' : 'Better Vision Opticals',
    brand: 'BETTER_VISION' as any,
    gstin: '', // Fetched from store setup in production
    address: '',
    city: store.store_id?.includes('BOK') ? 'Bokaro Steel City' : store.store_id?.includes('DHB') ? 'Dhanbad' : store.store_id?.includes('PUN') ? 'Pune' : '',
    state: store.store_id?.includes('PUN') ? 'Maharashtra' : 'Jharkhand',
    stateCode: store.store_id?.includes('PUN') ? '27' : '20',
    pincode: '',
    latitude: 0, longitude: 0, geoFenceRadius: 0,
    isActive: true, isHQ: false,
    enabledCategories: [],
    openingTime: '10:00', closingTime: '21:00',
  }), [store.store_id]);

  return (
    <div className="max-w-md mx-auto text-center py-8 space-y-6">
      <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto"><CheckCircle className="w-10 h-10 text-green-500" /></div>
      <div><h2 className="text-2xl font-bold text-gray-900">Order Created!</h2><p className="text-gray-500 mt-1">Order #{store.order_number}</p></div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 text-left space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Customer</span><span className="font-medium">{store.customer?.name}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Items</span><span className="font-medium">{(store.cart || []).length}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Total</span><span className="font-bold text-lg">₹{store.getGrandTotal().toLocaleString('en-IN')}</span></div>
        <div className="flex justify-between"><span className="text-gray-500">Paid</span><span className="font-medium text-green-600">₹{store.getTotalPaid().toLocaleString('en-IN')}</span></div>
        {store.getBalance() > 0 && <div className="flex justify-between"><span className="text-gray-500">Balance due</span><span className="font-medium text-red-600">₹{store.getBalance().toLocaleString('en-IN')}</span></div>}
        {store.sale_type === 'prescription_order' && <div className="flex justify-between"><span className="text-gray-500">Type</span><span className="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs font-medium">Rx Order → Workshop</span></div>}
      </div>

      {/* Incentive qualifying items — auto-tagged for kicker tracking */}
      {(() => {
        const INCENTIVE_KEYS = ['ZEISS', 'SAFILO', 'CARRERA', 'POLAROID', 'MARC JACOB', 'HUGO', 'SEVENTH STREET', 'BOSS', 'TOMMY HILFIGER', 'PIERRE CARDIN', 'UNDER ARMOUR'];
        const qualifying = (store.cart || []).filter(i => {
          const b = (i.brand || '').toUpperCase();
          const sb = (i.subbrand || '').toUpperCase();
          const n = (i.name || '').toUpperCase();
          return INCENTIVE_KEYS.some(k => b.includes(k) || sb.includes(k) || n.includes(k));
        });
        if (qualifying.length === 0) return null;
        return (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-left text-xs">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-amber-600" />
              <span className="font-semibold text-amber-800">Incentive-qualifying items ({qualifying.length})</span>
              <span className="text-amber-400 ml-auto">Auto-tagged at POS</span>
            </div>
            <div className="space-y-1.5">
              {qualifying.map(item => {
                const brandLabel = item.brand || 'Unknown';
                const subLabel = item.subbrand ? ` · ${item.subbrand}` : '';
                return (
                  <div key={item.id} className="flex items-center justify-between gap-2 bg-white/60 rounded-lg px-2.5 py-1.5">
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-amber-900 truncate block">{brandLabel}{subLabel}</span>
                      <span className="text-amber-500 truncate block">{item.name}</span>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <span className="font-semibold text-amber-800">₹{item.line_total.toLocaleString('en-IN')}</span>
                      {item.discount_percent > 0 && (
                        <span className="ml-1.5 text-red-500">-{item.discount_percent}%</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      <div className="flex gap-3 justify-center flex-wrap">
        <button onClick={onPrint} className="flex items-center gap-2 px-4 py-2.5 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50"><Printer className="w-4 h-4" /> Receipt</button>
        <button onClick={() => setShowGSTInvoice(true)} className="flex items-center gap-2 px-4 py-2.5 border border-blue-300 bg-blue-50 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-100"><FileText className="w-4 h-4" /> Tax Invoice</button>
        <button onClick={onReset} className="flex items-center gap-2 px-6 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600"><Plus className="w-4 h-4" /> New Sale</button>
      </div>

      {showGSTInvoice && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between no-print">
              <h3 className="font-semibold text-gray-900">GST Tax Invoice</h3>
              <button onClick={() => setShowGSTInvoice(false)} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-4">
              <GSTInvoice order={orderForInvoice as any} store={storeForInvoice as any} onPrint={() => setShowGSTInvoice(false)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Cart Sidebar
// ============================================================================
function CartSidebar() {
  const store = usePOSStore();
  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-gray-200">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2"><ShoppingCart className="w-4 h-4" /> Cart ({(store.cart || []).length})</h3>
        {store.salesperson_name && <p className="text-[10px] text-gray-400 mt-0.5">Sales: {store.salesperson_name}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {(store.cart || []).map(item => (
          <div key={item.id} className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0"><p className="text-sm font-medium text-gray-900 truncate">{item.name}</p><p className="text-xs text-gray-500">{item.brand}</p>
                {item.lens_details && <p className="text-xs text-purple-500">{item.lens_details.type}</p>}
              </div>
              <button onClick={() => store.removeFromCart(item.id)} className="text-gray-400 hover:text-red-500 ml-2"><X className="w-3.5 h-3.5" /></button>
            </div>
            <div className="flex items-center justify-between mt-2">
              <div className="flex items-center gap-1">
                <button onClick={() => store.updateQuantity(item.id, item.quantity - 1)} className="w-6 h-6 rounded bg-white border text-xs hover:bg-gray-100">-</button>
                <input type="number" min="1" max="99" value={item.quantity}
                  onChange={(e) => { const v = parseInt(e.target.value) || 1; store.updateQuantity(item.id, Math.max(1, Math.min(99, v))); }}
                  onFocus={(e) => e.target.select()}
                  className="w-10 text-center text-xs font-medium border border-gray-200 rounded px-1 py-0.5 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
                <button onClick={() => store.updateQuantity(item.id, item.quantity + 1)} className="w-6 h-6 rounded bg-white border text-xs hover:bg-gray-100">+</button>
              </div>
              <div className="text-right">{item.discount_percent > 0 && <span className="text-xs text-green-600 mr-1">-{item.discount_percent}%</span>}<span className="text-sm font-semibold">₹{Math.round(item.line_total).toLocaleString('en-IN')}</span></div>
            </div>
          </div>
        ))}
      </div>
      <div className="border-t border-gray-200 p-4 space-y-1 text-sm">
        <div className="flex justify-between text-gray-500"><span>Subtotal</span><span>₹{Math.round(store.getSubtotal()).toLocaleString('en-IN')}</span></div>
        {store.getTotalDiscount() > 0 && <div className="flex justify-between text-green-600"><span>Discount</span><span>-₹{Math.round(store.getTotalDiscount()).toLocaleString('en-IN')}</span></div>}
        <div className="flex justify-between text-gray-500"><span>GST</span><span>₹{Math.round(store.getGrandTotal() - store.getSubtotal() + store.getTotalDiscount()).toLocaleString('en-IN')}</span></div>
        <div className="flex justify-between font-bold text-base pt-1 border-t border-gray-200"><span>Total (incl. GST)</span><span className="text-bv-gold-600">₹{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}</span></div>
      </div>
    </div>
  );
}

export default POSLayout;
